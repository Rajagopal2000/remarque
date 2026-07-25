import json
import sqlite3
import time

from fastapi.testclient import TestClient


class ScriptedProvider:
    name = "fake"
    supports_sessions = True

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.prompts = []

    def transcribe(self, ink_png):
        return "x"

    def answer_events(self, prompt, system, resume, images=None):
        self.prompts.append(prompt)
        yield ("text", self.outputs.pop(0))


def _make_client(monkeypatch, tmp_path, provider):
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


def _run_anki(client, doc_id="docx"):
    job_id = client.post(f"/api/anki/{doc_id}").json()["job_id"]
    for _ in range(100):
        snap = client.get(f"/api/answer/{job_id}").json()
        if snap["status"] != "running":
            break
        time.sleep(0.02)
    assert snap["status"] == "done", snap["error"]
    return snap


def test_auto_tick_updates_only_existing_decks(monkeypatch, tmp_path):
    provider = ScriptedProvider(
        [
            '[{"type": "basic", "front": "Old", "back": "B"}]',
            '[{"type": "basic", "front": "New", "back": "B2"}]',
        ]
    )
    client, main = _make_client(monkeypatch, tmp_path, provider)
    main.history.add("docx", "user", "Q1")
    main.history.add("docx", "assistant", "A1")
    # A second document's history with no deck: the scheduler must ignore it.
    main.history.add("other-doc", "user", "Q2")
    main.history.add("other-doc", "assistant", "A2")

    _run_anki(client)
    assert len(main.anki_state.get("docx").cards) == 1

    # Nothing new: the tick is a no-op with no LLM call.
    main._anki_auto_tick()
    assert len(provider.prompts) == 1

    # New conversation content: the tick adds a card to the existing deck only.
    main.history.add("docx", "user", "Q3")
    main.history.add("docx", "assistant", "A3")
    main._anki_auto_tick()
    assert len(provider.prompts) == 2
    assert "UPDATE run" in provider.prompts[1]
    assert len(main.anki_state.get("docx").cards) == 2
    assert main.anki_state.get("other-doc") is None
    assert (tmp_path / "anki" / "docx.apkg").exists()


def test_auto_tick_survives_one_failing_deck(monkeypatch, tmp_path):
    provider = ScriptedProvider(
        [
            '[{"type": "basic", "front": "A", "back": "1"}]',
            '[{"type": "basic", "front": "B", "back": "2"}]',
            "not json at all",
            '[{"type": "basic", "front": "C", "back": "3"}]',
        ]
    )
    client, main = _make_client(monkeypatch, tmp_path, provider)
    for doc_id in ("doc-a", "doc-b"):
        main.history.add(doc_id, "user", "Q")
        main.history.add(doc_id, "assistant", "A")
        _run_anki(client, doc_id)

    for doc_id in ("doc-a", "doc-b"):
        main.history.add(doc_id, "user", "Q new")
        main.history.add(doc_id, "assistant", "A new")

    # First deck's update returns garbage; the second must still be processed.
    main._anki_auto_tick()
    totals = sorted(len(main.anki_state.get(d).cards) for d in ("doc-a", "doc-b"))
    assert totals == [1, 2]


def test_auto_tick_skips_deck_for_deleted_document(monkeypatch, tmp_path):
    from app import anki as anki_mod

    provider = ScriptedProvider([])
    client, main = _make_client(monkeypatch, tmp_path, provider)
    # A deck whose document was deleted from the tablet and that has no chat
    # history: nothing to generate from, so the tick must not touch it.
    old_hash = anki_mod.text_hash("the old document text")
    main.anki_state.save(
        "ghost",
        anki_mod.DeckState(
            cards=[{"type": "basic", "front": "F", "back": "B"}],
            last_history_ts=0.0,
            highlight_texts=[],
            doc_text_hash=old_hash,
            margin_texts=[],
        ),
    )
    main._anki_auto_tick()
    assert provider.prompts == []
    assert main.anki_state.get("ghost").doc_text_hash == old_hash


def _write_doc(tmp_path, doc_id):
    (tmp_path / f"{doc_id}.metadata").write_text(
        json.dumps(
            {
                "type": "DocumentType",
                "visibleName": "Paper",
                "lastOpened": "1700000000000",
                "lastOpenedPage": 0,
            }
        )
    )
    (tmp_path / f"{doc_id}.content").write_text(json.dumps({"pageCount": 1, "fileType": "pdf"}))


def test_auto_tick_treats_legacy_deck_margins_as_covered(monkeypatch, tmp_path):
    provider = ScriptedProvider(['[{"type": "basic", "front": "Old", "back": "B"}]'])
    client, main = _make_client(monkeypatch, tmp_path, provider)
    _write_doc(tmp_path, "docx")
    monkeypatch.setattr(
        main.marginalia, "doc_notes", lambda *a, **kw: [{"text": "check eq 3"}]
    )
    main.history.add("docx", "user", "Q1")
    main.history.add("docx", "assistant", "A1")
    _run_anki(client)

    # Simulate a deck row created before margin tracking existed (NULL column).
    with sqlite3.connect(tmp_path / "history.db") as conn:
        conn.execute("UPDATE anki_decks SET margin_texts = NULL WHERE doc_id = 'docx'")
    assert main.anki_state.get("docx").margin_texts is None

    # The pre-existing margin note must count as covered: no LLM call.
    main._anki_auto_tick()
    assert len(provider.prompts) == 1
