import time
import zipfile
from io import BytesIO

from app.anki import _clean_json_array, build_apkg, generate_cards, validate_cards


def test_clean_json_array_strips_fences():
    text = 'Here you go:\n```json\n[{"type": "basic", "front": "Q", "back": "A"}]\n```'
    assert _clean_json_array(text) == [{"type": "basic", "front": "Q", "back": "A"}]


def test_validate_cards_filters_bad_entries():
    cards = validate_cards(
        [
            {"type": "basic", "front": "Q", "back": "A"},
            {"type": "reversed", "front": "term", "back": "definition"},
            {"type": "cloze", "text": "Entropy is {{c1::disorder}}."},
            {"type": "cloze", "text": "no marker here"},  # dropped
            {"type": "basic", "front": "no back"},  # dropped
            {"type": "weird", "front": "x", "back": "y"},  # dropped
        ]
    )
    assert [c["type"] for c in cards] == ["basic", "reversed", "cloze"]


def test_generate_cards_includes_doc_and_chat():
    class P:
        def answer_events(self, prompt, system, resume, images=None):
            self.prompt = prompt
            self.system = system
            yield ("text", '[{"type": "basic", "front": "F", "back": "B"}]')

    provider = P()
    turns = [
        {"role": "user", "content": "What is entropy?", "ts": 1.0},
        {"role": "assistant", "content": "A measure of disorder.", "ts": 2.0},
    ]
    cards = generate_cards(provider, "Paper", "Document body text.", turns)
    assert cards == [{"type": "basic", "front": "F", "back": "B"}]
    assert "<document>" in provider.prompt
    assert "Document body text." in provider.prompt
    assert "<conversation>" in provider.prompt
    assert "User asked: What is entropy?" in provider.prompt
    assert "highest-priority source" in provider.system


def test_generate_cards_chat_only():
    class P:
        def answer_events(self, prompt, system, resume, images=None):
            self.prompt = prompt
            yield ("text", '[{"type": "cloze", "text": "Entropy is {{c1::disorder}}."}]')

    provider = P()
    turns = [{"role": "user", "content": "Q", "ts": 1.0}]
    cards = generate_cards(provider, "General questions", None, turns)
    assert cards[0]["type"] == "cloze"
    assert "<document>" not in provider.prompt
    assert "<conversation>" in provider.prompt


def test_build_apkg_is_zip_with_collection():
    cards = [
        {"type": "basic", "front": "Q", "back": "A"},
        {"type": "reversed", "front": "term", "back": "def"},
        {"type": "cloze", "text": "X is {{c1::Y}}."},
    ]
    data = build_apkg("My Paper", cards)
    with zipfile.ZipFile(BytesIO(data)) as z:
        names = z.namelist()
    assert any(n.startswith("collection.anki2") for n in names)


def _make_client(monkeypatch, tmp_path, provider):
    from fastapi.testclient import TestClient

    from app import anki as anki_mod
    from app import main
    from app.config import settings
    from app.history import History
    from app.sessions import SessionStore

    monkeypatch.setattr(settings, "sync_dir", tmp_path)
    monkeypatch.setattr(settings, "agent_home", tmp_path / "agent-home")
    (tmp_path / "agent-home").mkdir(exist_ok=True)
    monkeypatch.setattr(main, "run_sync", lambda **kw: 0.0)
    main.history = History(tmp_path / "history.db")
    main.sessions = SessionStore(tmp_path / "history.db", ttl_days=60)
    main.anki_state = anki_mod.AnkiStateStore(tmp_path / "history.db")
    monkeypatch.setattr(main, "get_provider", lambda name: provider)
    return TestClient(main.app), main


def _run_anki(client, doc_id="docx", mode="auto"):
    resp = client.post(f"/api/anki/{doc_id}?mode={mode}")
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]
    for _ in range(100):
        snap = client.get(f"/api/answer/{job_id}").json()
        if snap["status"] != "running":
            break
        time.sleep(0.02)
    assert snap["status"] == "done", snap["error"]
    return snap


class ScriptedProvider:
    name = "fake"
    supports_sessions = True

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.prompts = []

    def answer_events(self, prompt, system, resume, images=None):
        self.prompts.append(prompt)
        yield ("text", self.outputs.pop(0))


def test_anki_endpoint_generates_deck(monkeypatch, tmp_path):
    provider = ScriptedProvider(['[{"type": "basic", "front": "F", "back": "B"}]'])
    client, main = _make_client(monkeypatch, tmp_path, provider)
    main.history.add("docx", "user", "What is entropy?")
    main.history.add("docx", "assistant", "Disorder.")

    snap = _run_anki(client)
    assert "Created 1 cards" in snap["text_so_far"]

    deck = client.get("/api/anki/docx.apkg")
    assert deck.status_code == 200
    assert deck.content[:2] == b"PK"


def test_anki_incremental_update(monkeypatch, tmp_path):
    provider = ScriptedProvider(
        [
            '[{"type": "basic", "front": "Old card", "back": "B"}]',
            '[{"type": "cloze", "text": "New fact {{c1::X}}."}]',
        ]
    )
    client, main = _make_client(monkeypatch, tmp_path, provider)
    main.history.add("docx", "user", "What is entropy?")
    main.history.add("docx", "assistant", "Disorder.")

    snap = _run_anki(client)
    assert "Created 1 cards" in snap["text_so_far"]

    # Run again with nothing new: no LLM call, no change.
    snap = _run_anki(client)
    assert "No new content" in snap["text_so_far"]
    assert len(provider.prompts) == 1

    # New chat turn arrives; update run sends only it, plus existing fronts.
    main.history.add("docx", "user", "What is a Carnot engine?")
    main.history.add("docx", "assistant", "An ideal heat engine.")
    snap = _run_anki(client)
    assert "Added 1 cards" in snap["text_so_far"]
    assert "deck total 2" in snap["text_so_far"]
    update_prompt = provider.prompts[1]
    assert "UPDATE run" in update_prompt
    assert "Old card" in update_prompt  # existing fronts listed
    assert "Carnot" in update_prompt  # new turn included
    assert "What is entropy?" not in update_prompt  # old turn excluded


def test_anki_mode_full_regenerates(monkeypatch, tmp_path):
    provider = ScriptedProvider(
        [
            '[{"type": "basic", "front": "A", "back": "1"}]',
            '[{"type": "basic", "front": "B", "back": "2"}]',
        ]
    )
    client, main = _make_client(monkeypatch, tmp_path, provider)
    main.history.add("docx", "user", "Q")
    main.history.add("docx", "assistant", "A")

    _run_anki(client)
    snap = _run_anki(client, mode="full")
    assert "Created 1 cards" in snap["text_so_far"]
    assert "UPDATE run" not in provider.prompts[1]


def test_push_via_ankiconnect(monkeypatch):
    from app import anki

    calls = []

    def fake_connect(action, **params):
        calls.append(action)
        if action == "addNotes":
            return [111, None]  # one added, one duplicate
        return None

    monkeypatch.setattr(anki, "_anki_connect", fake_connect)
    added = anki.push_via_ankiconnect(
        "T",
        [
            {"type": "basic", "front": "Q", "back": "A"},
            {"type": "cloze", "text": "X {{c1::Y}}"},
        ],
    )
    assert added == 1
    assert calls == ["sync", "createDeck", "addNotes", "sync"]
