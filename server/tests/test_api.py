import time

import pytest
from fastapi.testclient import TestClient


class FakeSessionProvider:
    """Session-capable provider that records what it was asked to do."""

    name = "fake"
    supports_sessions = True

    def __init__(self):
        self.calls = []

    def transcribe(self, ink_png):
        assert ink_png[:8] == b"\x89PNG\r\n\x1a\n"
        return "hello?"

    def answer_events(self, prompt, system, resume_session_id, images=None):
        self.calls.append({"prompt": prompt, "resume": resume_session_id, "images": images})
        yield ("text", "Hi there.")
        yield ("session", "sess-1")
        yield ("usage", {"input_tokens": 100, "output_tokens": 5})


@pytest.fixture
def env(monkeypatch, tmp_path):
    from app import main
    from app.config import settings
    from app.history import History
    from app.sessions import SessionStore

    monkeypatch.setattr(settings, "sync_dir", tmp_path)
    monkeypatch.setattr(main, "run_sync", lambda **kw: 0.0)
    main.history = History(tmp_path / "history.db")
    main.sessions = SessionStore(tmp_path / "history.db", ttl_days=60)

    provider = FakeSessionProvider()
    monkeypatch.setattr(main, "get_provider", lambda name: provider)
    return TestClient(main.app), provider


def _ask_and_wait(client):
    resp = client.post(
        "/api/ask",
        json={"strokes": [[[1, 1], [2, 2]]], "canvas_w": 100, "canvas_h": 100},
    )
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]
    for _ in range(100):
        snap = client.get(f"/api/answer/{job_id}").json()
        if snap["status"] != "running":
            break
        time.sleep(0.02)
    return snap


def test_healthz(env):
    client, _ = env
    assert client.get("/healthz").json() == {"ok": True}


def test_ask_creates_then_resumes_session(env):
    client, provider = env

    snap = _ask_and_wait(client)
    assert snap["status"] == "done"
    assert snap["question_read"] == "hello?"
    assert "Hi there." in snap["text_so_far"]
    assert snap["usage"]["input_tokens"] == 100
    assert snap["session"]["exists"] is True
    # First ask: no resume, seed included
    assert provider.calls[0]["resume"] is None

    snap = _ask_and_wait(client)
    assert snap["status"] == "done"
    # Second ask: resumed with the stored session id, no re-seeding
    assert provider.calls[1]["resume"] == "sess-1"
    assert "This session is a conversation" not in provider.calls[1]["prompt"]
    assert "This session is a conversation" in provider.calls[0]["prompt"]


def test_clear_session_starts_fresh(env):
    client, provider = env
    _ask_and_wait(client)
    resp = client.post("/api/session/__no_document__/clear")
    assert resp.json()["cleared"] == 1
    assert resp.json()["exists"] is False

    _ask_and_wait(client)
    assert provider.calls[-1]["resume"] is None  # new session after clear


def test_history_recorded(env):
    client, _ = env
    _ask_and_wait(client)
    turns = client.get("/api/history/__no_document__").json()["turns"]
    assert [t["role"] for t in turns] == ["user", "assistant"]
    assert turns[0]["content"] == "hello?"
    assert all(isinstance(t["ts"], float) for t in turns)


def test_ask_rejects_empty_strokes(env):
    client, _ = env
    resp = client.post("/api/ask", json={"strokes": [], "canvas_w": 100, "canvas_h": 100})
    assert resp.status_code == 400


def test_auth_required_when_token_set(env, monkeypatch):
    client, _ = env
    from app.config import settings

    monkeypatch.setattr(settings, "api_token", "secret123")
    body = {"strokes": [[[1, 1], [2, 2]]], "canvas_w": 100, "canvas_h": 100}
    assert client.post("/api/ask", json=body).status_code == 401
    assert client.post("/api/ask", json=body, headers={"X-Api-Token": "wrong"}).status_code == 401
    assert client.post("/api/ask", json=body, headers={"X-Api-Token": "secret123"}).status_code == 200
    # healthz stays open for probes
    assert client.get("/healthz").status_code == 200


def test_quick_action_no_document(env):
    client, _ = env
    resp = client.post("/api/quick", json={"action": "summarize_doc"})
    assert resp.status_code == 404


def test_export_requires_history(env):
    client, _ = env
    assert client.post("/api/export/unknown-doc?push=false").status_code == 404


def test_export_pdf_download(env):
    client, _ = env
    _ask_and_wait(client)
    resp = client.post("/api/export/__no_document__?push=false")
    assert resp.status_code == 200
    assert resp.json()["pushed"] is False
    assert resp.json()["pdf_bytes"] > 500

    pdf = client.get("/api/export/__no_document__.pdf")
    assert pdf.status_code == 200
    assert pdf.content[:5] == b"%PDF-"
