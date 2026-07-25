import json
import time
import uuid

import pymupdf
import pytest
from fastapi.testclient import TestClient


class QuizProvider:
    """Yields a scripted quiz question, then a scripted grade."""

    name = "fake"
    supports_sessions = True

    def __init__(self):
        self.calls = []
        self.outputs = ["What bounds the efficiency of a heat engine?", "Correct. The Carnot limit."]

    def transcribe(self, ink_png):
        return "the carnot limit"

    def answer_events(self, prompt, system, resume_session_id, images=None):
        self.calls.append({"prompt": prompt, "resume": resume_session_id})
        yield ("text", self.outputs.pop(0))
        yield ("session", "sess-1")


@pytest.fixture
def env(monkeypatch, tmp_path):
    from app import main
    from app.config import settings
    from app.history import History
    from app.sessions import SessionStore

    doc_id = str(uuid.uuid4())
    pdf = pymupdf.open()
    pdf.new_page().insert_text((72, 72), "Heat engines and the Carnot limit.")
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
    (tmp_path / f"{doc_id}.content").write_text(json.dumps({"pageCount": 1, "fileType": "pdf"}))

    monkeypatch.setattr(settings, "sync_dir", tmp_path)
    monkeypatch.setattr(main, "run_sync", lambda **kw: 0.0)
    main.history = History(tmp_path / "history.db")
    main.sessions = SessionStore(tmp_path / "history.db", ttl_days=60)
    main.quiz_pending.clear()

    provider = QuizProvider()
    monkeypatch.setattr(main, "get_provider", lambda name: provider)
    return TestClient(main.app), provider, doc_id


def _wait(client, job_id):
    for _ in range(200):
        snap = client.get(f"/api/answer/{job_id}").json()
        if snap["status"] != "running":
            return snap
        time.sleep(0.02)
    return snap


INK = {"strokes": [[[1, 1], [2, 2]]], "canvas_w": 100, "canvas_h": 100}


def test_quiz_flow_ask_answer_grade(env):
    client, provider, doc_id = env

    resp = client.post("/api/quiz", json={})
    assert resp.status_code == 200
    snap = _wait(client, resp.json()["job_id"])
    assert snap["status"] == "done"
    assert "efficiency of a heat engine" in snap["text_so_far"]
    # The model got the quiz instruction; the display question stays short.
    assert "exactly one exam-style question" in provider.calls[0]["prompt"]
    assert snap["question_read"] == "Quiz me"

    resp = client.post("/api/quiz/answer", json=INK)
    assert resp.status_code == 200
    snap = _wait(client, resp.json()["job_id"])
    assert snap["status"] == "done"
    assert snap["question_read"] == "the carnot limit"
    assert "Correct" in snap["text_so_far"]
    grade_prompt = provider.calls[1]["prompt"]
    assert "What bounds the efficiency of a heat engine?" in grade_prompt
    assert "the carnot limit" in grade_prompt
    assert "Grade my answer" in grade_prompt

    # The whole exchange lands in history (feeds Anki generation later).
    turns = client.get(f"/api/history/{doc_id}").json()["turns"]
    assert [t["role"] for t in turns] == ["user", "assistant", "user", "assistant"]
    assert turns[1]["content"] == "What bounds the efficiency of a heat engine?"
    assert turns[2]["content"] == "the carnot limit"


def test_quiz_answer_without_pending_question(env):
    client, _, _ = env
    resp = client.post("/api/quiz/answer", json=INK)
    assert resp.status_code == 409


def test_quiz_answer_consumes_pending_question(env):
    client, provider, _ = env
    _wait(client, client.post("/api/quiz", json={}).json()["job_id"])
    _wait(client, client.post("/api/quiz/answer", json=INK).json()["job_id"])
    # Question consumed: a second answer has nothing to grade against.
    assert client.post("/api/quiz/answer", json=INK).status_code == 409
