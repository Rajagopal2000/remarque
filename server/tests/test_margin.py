import json
import time
import uuid

import pymupdf
import pytest
from fastapi.testclient import TestClient
from rmscene import scene_items as si
from rmscene import write_blocks
from rmscene.crdt_sequence import CrdtSequenceItem
from rmscene.scene_stream import SceneLineItemBlock
from rmscene.tagged_block_common import CrdtId

from app import marginalia


def _line_block(tool: si.Pen, points: list[tuple[float, float]], seq: int) -> SceneLineItemBlock:
    line = si.Line(
        color=si.PenColor.BLACK,
        tool=tool,
        points=[
            si.Point(x=x, y=y, speed=1, direction=0, width=2, pressure=50) for x, y in points
        ],
        thickness_scale=1.0,
        starting_length=0.0,
    )
    item = CrdtSequenceItem(
        item_id=CrdtId(1, 10 + seq),
        left_id=CrdtId(0, 0),
        right_id=CrdtId(0, 0),
        deleted_length=0,
        value=line,
    )
    return SceneLineItemBlock(parent_id=CrdtId(0, 1), item=item)


def write_rm(path, lines: list[tuple[si.Pen, list[tuple[float, float]]]]) -> None:
    blocks = [_line_block(tool, points, i) for i, (tool, points) in enumerate(lines)]
    with path.open("wb") as f:
        write_blocks(f, blocks)


class CountingTranscriber:
    def __init__(self, text="why entropy?"):
        self.text = text
        self.calls = 0

    def transcribe(self, ink_png):
        assert ink_png[:8] == b"\x89PNG\r\n\x1a\n"
        self.calls += 1
        return self.text


PEN_LINE = [(10.0, 10.0), (60.0, 15.0), (90.0, 40.0)]


def test_page_strokes_excludes_highlighter_and_eraser(tmp_path):
    rm = tmp_path / "page.rm"
    write_rm(
        rm,
        [
            (si.Pen.FINELINER_2, PEN_LINE),
            (si.Pen.HIGHLIGHTER_1, [(0.0, 0.0), (100.0, 0.0)]),
            (si.Pen.ERASER, [(5.0, 5.0), (6.0, 6.0)]),
        ],
    )
    strokes = marginalia._page_strokes(rm)
    assert strokes == [[[x, y] for x, y in PEN_LINE]]


def test_page_note_caches_by_ink_hash(tmp_path):
    doc_id, page_id = "doc1", "page1"
    (tmp_path / doc_id).mkdir()
    rm = tmp_path / doc_id / f"{page_id}.rm"
    write_rm(rm, [(si.Pen.FINELINER_2, PEN_LINE)])

    store = marginalia.MarginNoteStore(tmp_path / "db.sqlite")
    transcriber = CountingTranscriber()

    assert marginalia.page_note(store, transcriber, tmp_path, doc_id, page_id) == "why entropy?"
    assert transcriber.calls == 1
    # Unchanged ink: served from cache, no second vision call.
    assert marginalia.page_note(store, transcriber, tmp_path, doc_id, page_id) == "why entropy?"
    assert transcriber.calls == 1
    # Changed ink: re-transcribed.
    write_rm(rm, [(si.Pen.FINELINER_2, PEN_LINE), (si.Pen.FINELINER_2, [(0.0, 50.0), (30.0, 55.0)])])
    transcriber.text = "why entropy? see eq 3"
    assert (
        marginalia.page_note(store, transcriber, tmp_path, doc_id, page_id)
        == "why entropy? see eq 3"
    )
    assert transcriber.calls == 2


def test_page_note_no_ink_and_garbage_file(tmp_path):
    doc_id = "doc1"
    (tmp_path / doc_id).mkdir()
    store = marginalia.MarginNoteStore(tmp_path / "db.sqlite")
    transcriber = CountingTranscriber()

    assert marginalia.page_note(store, transcriber, tmp_path, doc_id, "missing") is None
    (tmp_path / doc_id / "bad.rm").write_bytes(b"not a real rm file")
    assert marginalia.page_note(store, transcriber, tmp_path, doc_id, "bad") is None
    highlighter_only = tmp_path / doc_id / "hl.rm"
    write_rm(highlighter_only, [(si.Pen.HIGHLIGHTER_2, [(0.0, 0.0), (50.0, 0.0)])])
    assert marginalia.page_note(store, transcriber, tmp_path, doc_id, "hl") is None
    assert transcriber.calls == 0


def test_doc_notes_isolates_page_failures(tmp_path, monkeypatch):
    doc_id = "doc1"
    (tmp_path / doc_id).mkdir()
    content = {"cPages": {"pages": [{"id": "p1"}, {"id": "p2"}]}}
    write_rm(tmp_path / doc_id / "p1.rm", [(si.Pen.FINELINER_2, PEN_LINE)])
    write_rm(tmp_path / doc_id / "p2.rm", [(si.Pen.FINELINER_2, [(0.0, 0.0), (20.0, 20.0)])])

    store = marginalia.MarginNoteStore(tmp_path / "db.sqlite")

    class FlakyTranscriber:
        calls = 0

        def transcribe(self, ink_png):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("vision model down")
            return "note on p2"

    notes = marginalia.doc_notes(store, FlakyTranscriber(), tmp_path, doc_id, content)
    assert notes == [{"page_index": 1, "text": "note on p2"}]


class MarginProvider:
    name = "fake"
    supports_sessions = True

    def __init__(self):
        self.calls = []

    def transcribe(self, ink_png):
        return "what is this?"

    def answer_events(self, prompt, system, resume_session_id, images=None):
        self.calls.append({"prompt": prompt})
        yield ("text", "Answer.")
        yield ("session", "sess-1")


@pytest.fixture
def env(monkeypatch, tmp_path):
    from app import main
    from app.config import settings
    from app.history import History
    from app.sessions import SessionStore

    doc_id = str(uuid.uuid4())
    page_id = str(uuid.uuid4())
    pdf = pymupdf.open()
    pdf.new_page().insert_text((72, 72), "Some page text.")
    pdf.save(tmp_path / f"{doc_id}.pdf")
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
    (tmp_path / f"{doc_id}.content").write_text(
        json.dumps(
            {
                "pageCount": 1,
                "fileType": "pdf",
                "cPages": {"pages": [{"id": page_id, "redir": {"value": 0}}]},
            }
        )
    )
    (tmp_path / doc_id).mkdir()
    write_rm(tmp_path / doc_id / f"{page_id}.rm", [(si.Pen.FINELINER_2, PEN_LINE)])

    monkeypatch.setattr(settings, "sync_dir", tmp_path)
    monkeypatch.setattr(main, "run_sync", lambda **kw: 0.0)
    main.history = History(tmp_path / "history.db")
    main.sessions = SessionStore(tmp_path / "history.db", ttl_days=60)
    main.margin_notes = marginalia.MarginNoteStore(tmp_path / "history.db")

    provider = MarginProvider()
    monkeypatch.setattr(main, "get_provider", lambda name: provider)
    return TestClient(main.app), provider


def _ask_and_wait(client):
    resp = client.post(
        "/api/ask", json={"strokes": [[[1, 1], [2, 2]]], "canvas_w": 100, "canvas_h": 100}
    )
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]
    for _ in range(200):
        snap = client.get(f"/api/answer/{job_id}").json()
        if snap["status"] != "running":
            return snap
        time.sleep(0.02)
    return snap


def test_ask_includes_current_page_margin_note(env):
    client, provider = env
    snap = _ask_and_wait(client)
    assert snap["status"] == "done"
    prompt = provider.calls[-1]["prompt"]
    assert "Notes the user handwrote on the current page" in prompt
    # The transcriber fake returns the same text for question ink and margin ink.
    assert "what is this?" in prompt


class ScriptedCardProvider:
    name = "fake"
    supports_sessions = True

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.prompts = []
        self.margin_text = "check equation 3"

    def transcribe(self, ink_png):
        return self.margin_text

    def answer_events(self, prompt, system, resume, images=None):
        self.prompts.append(prompt)
        yield ("text", self.outputs.pop(0))


def _run_anki(client, doc_id):
    resp = client.post(f"/api/anki/{doc_id}")
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]
    for _ in range(200):
        snap = client.get(f"/api/answer/{job_id}").json()
        if snap["status"] != "running":
            break
        time.sleep(0.02)
    assert snap["status"] == "done", snap["error"]
    return snap


def test_anki_covers_margin_notes_and_updates_on_new_ink(monkeypatch, tmp_path, env):
    from app import anki as anki_mod
    from app import main
    from app.config import settings

    client, _ = env
    provider = ScriptedCardProvider(
        [
            '[{"type": "basic", "front": "F", "back": "B"}]',
            '[{"type": "basic", "front": "F2", "back": "B2"}]',
        ]
    )
    monkeypatch.setattr(main, "get_provider", lambda name: provider)
    monkeypatch.setattr(settings, "agent_home", tmp_path / "agent-home")
    main.anki_state = anki_mod.AnkiStateStore(settings.sync_dir / "history.db")

    from app import documents

    doc = documents.current_document(settings.sync_dir)
    page_id = documents.page_ids(doc.content)[0]

    snap = _run_anki(client, doc.doc_id)
    assert "Created 1 cards" in snap["text_so_far"]
    assert "check equation 3" in provider.prompts[0]
    assert "Notes the user handwrote in the margins" in provider.prompts[0]

    # Nothing changed: no LLM call.
    snap = _run_anki(client, doc.doc_id)
    assert "No new content" in snap["text_so_far"]
    assert len(provider.prompts) == 1

    # New ink on the page: incremental run sends only the new note.
    write_rm(
        settings.sync_dir / doc.doc_id / f"{page_id}.rm",
        [(si.Pen.FINELINER_2, PEN_LINE), (si.Pen.FINELINER_2, [(0.0, 80.0), (40.0, 90.0)])],
    )
    provider.margin_text = "carnot cycle diagram"
    snap = _run_anki(client, doc.doc_id)
    assert "Added 1 cards" in snap["text_so_far"]
    update_prompt = provider.prompts[1]
    assert "carnot cycle diagram" in update_prompt
    assert "UPDATE run" in update_prompt
