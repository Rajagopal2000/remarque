import time

import pytest
from fastapi.testclient import TestClient

from app import sync
from app.prompting import build_ask


def test_sync_throttle(monkeypatch):
    from app.config import settings

    calls = []

    class FakeProc:
        returncode = 0
        stderr = ""

    monkeypatch.setattr(sync.subprocess, "run", lambda *a, **kw: calls.append(1) or FakeProc())
    monkeypatch.setattr(sync, "_last_success_monotonic", None)
    monkeypatch.setattr(settings, "sync_max_age", 60)

    sync.run_sync()
    assert len(calls) == 1
    assert sync.run_sync() == 0.0  # throttled, no rsync
    assert len(calls) == 1
    sync.run_sync(force=True)  # /api/refresh path always syncs
    assert len(calls) == 2
    assert sync.last_success_age() is not None


def test_brief_prompt():
    normal = build_ask("What is entropy?", None, None, None)
    brief = build_ask("What is entropy?", None, None, None, brief=True)
    assert "briefly" not in normal
    assert "Answer briefly" in brief


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

    class SlowProvider:
        name = "fake"
        supports_sessions = True

        def transcribe(self, ink_png):
            return "slow question"

        def answer_events(self, prompt, system, resume, images=None):
            self.prompt = prompt
            for i in range(30):
                yield ("text", f"chunk{i} ")
                time.sleep(0.05)
            yield ("session", "sess-1")

    provider = SlowProvider()
    monkeypatch.setattr(main, "get_provider", lambda name: provider)
    return TestClient(main.app), provider


def test_cancel_stops_job_and_skips_history(env):
    client, provider = env
    resp = client.post(
        "/api/ask", json={"strokes": [[[1, 1], [2, 2]]], "canvas_w": 100, "canvas_h": 100}
    )
    job_id = resp.json()["job_id"]
    # let it get into the answering phase, then cancel
    time.sleep(0.3)
    cancel = client.post(f"/api/answer/{job_id}/cancel")
    assert cancel.status_code == 200
    assert cancel.json()["status"] == "cancelled"
    time.sleep(0.3)
    snap = client.get(f"/api/answer/{job_id}").json()
    assert snap["status"] == "cancelled"
    turns = client.get("/api/history/__no_document__").json()["turns"]
    assert turns == []  # cancelled asks are not recorded


def test_brief_flag_reaches_prompt(env):
    client, provider = env
    resp = client.post(
        "/api/ask",
        json={"strokes": [[[1, 1], [2, 2]]], "canvas_w": 100, "canvas_h": 100, "brief": True},
    )
    job_id = resp.json()["job_id"]
    for _ in range(200):
        if client.get(f"/api/answer/{job_id}").json()["status"] != "running":
            break
        time.sleep(0.02)
    assert "Answer briefly" in provider.prompt


def test_metrics_endpoint(env):
    client, _ = env
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "remarque_asks_total" in resp.text
