"""Page ask: a question handwritten on the PDF page itself becomes the ask."""

import json
import time
import uuid

import pymupdf
import pytest
from fastapi.testclient import TestClient
from rmscene import scene_items as si

from test_margin import PEN_LINE, write_rm


class Provider:
    name = "fake"
    supports_sessions = True

    def __init__(self):
        self.calls = []
        self.transcribes = 0

    def transcribe(self, ink_png, strong=False):
        assert ink_png[:8] == b"\x89PNG\r\n\x1a\n"
        self.transcribes += 1
        return f"page question {self.transcribes}"

    def answer_events(self, prompt, system, resume_session_id, images=None):
        self.calls.append({"prompt": prompt, "resume": resume_session_id})
        yield ("text", "Answer.")
        yield ("session", "sess-1")


@pytest.fixture
def env(monkeypatch, tmp_path):
    from app import main, marginalia
    from app.config import settings
    from app.history import History
    from app.sessions import SessionStore

    doc_id = str(uuid.uuid4())
    page_id = str(uuid.uuid4())
    pdf = pymupdf.open()
    pdf.new_page().insert_text((72, 72), "The Carnot limit bounds engine efficiency.")
    pdf.save(tmp_path / f"{doc_id}.pdf")
    (tmp_path / f"{doc_id}.metadata").write_text(
        json.dumps(
            {
                "type": "DocumentType",
                "visibleName": "Paper",
                "lastOpened": str(int(time.time() * 1000)),
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

    monkeypatch.setattr(settings, "sync_dir", tmp_path)
    monkeypatch.setattr(main, "run_sync", lambda **kw: 0.0)
    db = tmp_path / "history.db"
    main.history = History(db)
    main.sessions = SessionStore(db, ttl_days=60)
    main.asked_ink = marginalia.AskedInkStore(db)

    provider = Provider()
    monkeypatch.setattr(main, "get_provider", lambda name: provider)
    return TestClient(main.app), provider, main, doc_id, page_id, tmp_path


def _wait(client, job_id):
    for _ in range(300):
        snap = client.get(f"/api/answer/{job_id}").json()
        if snap["status"] != "running":
            return snap
        time.sleep(0.02)
    return snap


def test_page_ask_transcribes_page_ink(env):
    client, provider, main, doc_id, page_id, tmp_path = env
    write_rm(tmp_path / doc_id / f"{page_id}.rm", [(si.Pen.FINELINER_2, PEN_LINE)])

    resp = client.post("/api/ask/page", json={})
    assert resp.status_code == 200
    snap = _wait(client, resp.json()["job_id"])
    assert snap["status"] == "done"
    assert snap["question_read"] == "page question 1"
    turns = client.get(f"/api/history/{doc_id}").json()["turns"]
    assert turns[0]["content"] == "page question 1"


def test_page_ask_without_ink_is_404(env):
    client, _, _, _, _, _ = env
    assert client.post("/api/ask/page", json={}).status_code == 404


def test_page_ask_consumes_ink_and_reads_only_new_strokes(env):
    client, provider, main, doc_id, page_id, tmp_path = env
    rm = tmp_path / doc_id / f"{page_id}.rm"
    write_rm(rm, [(si.Pen.FINELINER_2, PEN_LINE)])
    _wait(client, client.post("/api/ask/page", json={}).json()["job_id"])

    # Same ink again: nothing new to ask.
    assert client.post("/api/ask/page", json={}).status_code == 404

    # A second handwritten question on the same page: asked alone.
    second = [(30.0, 120.0), (80.0, 130.0)]
    write_rm(rm, [(si.Pen.FINELINER_2, PEN_LINE), (si.Pen.FINELINER_2, second)])
    resp = client.post("/api/ask/page", json={})
    assert resp.status_code == 200
    snap = _wait(client, resp.json()["job_id"])
    assert snap["status"] == "done"
    assert snap["question_read"] == "page question 2"
    assert provider.transcribes == 2


def _loop(x0, y0, x1, y1):
    xm, ym = (x0 + x1) / 2, (y0 + y1) / 2
    return [
        (x0, y0), (xm, y0), (x1, y0), (x1, ym), (x1, y1),
        (xm, y1), (x0, y1), (x0, ym), (x0 + 1, y0 + 1),
    ]


def test_circled_questions_are_asked_one_per_tap(env):
    client, provider, main, doc_id, page_id, tmp_path = env
    rm = tmp_path / doc_id / f"{page_id}.rm"
    q2 = [(10.0, 110.0), (60.0, 115.0), (90.0, 140.0)]
    notes = [(300.0, 300.0), (350.0, 310.0)]  # uncircled ink stays untouched
    write_rm(
        rm,
        [
            (si.Pen.FINELINER_2, PEN_LINE),
            (si.Pen.FINELINER_2, _loop(0, 0, 100, 50)),
            (si.Pen.FINELINER_2, q2),
            (si.Pen.FINELINER_2, _loop(0, 100, 100, 150)),
            (si.Pen.FINELINER_2, notes),
        ],
    )

    resp = client.post("/api/ask/page", json={})
    assert resp.status_code == 200
    assert resp.json()["circled_remaining"] == 1
    snap = _wait(client, resp.json()["job_id"])
    assert snap["status"] == "done"

    resp = client.post("/api/ask/page", json={})
    assert resp.status_code == 200
    assert resp.json()["circled_remaining"] == 0
    snap = _wait(client, resp.json()["job_id"])
    assert snap["status"] == "done"
    assert provider.transcribes == 2

    # Both circles consumed; the uncircled note ink is asked as the fallback.
    resp = client.post("/api/ask/page", json={})
    assert resp.status_code == 200
    _wait(client, resp.json()["job_id"])
    assert provider.transcribes == 3
    assert client.post("/api/ask/page", json={}).status_code == 404
