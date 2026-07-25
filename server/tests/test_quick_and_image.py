import json
import time
import uuid

import pymupdf
import pytest
from fastapi.testclient import TestClient


class RecordingProvider:
    name = "fake"
    supports_sessions = True

    def __init__(self):
        self.calls = []

    def transcribe(self, ink_png):
        return "what is this figure?"

    def answer_events(self, prompt, system, resume_session_id, images=None):
        self.calls.append({"prompt": prompt, "resume": resume_session_id, "images": images})
        yield ("text", "Answer.")
        yield ("session", "sess-1")


@pytest.fixture
def env(monkeypatch, tmp_path):
    from app import main
    from app.config import settings
    from app.history import History
    from app.sessions import SessionStore

    doc_id = str(uuid.uuid4())
    pdf = pymupdf.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "Thermodynamics is the study of heat.")
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
    monkeypatch.setattr(settings, "agent_home", tmp_path / "agent-home")
    monkeypatch.setattr(main, "run_sync", lambda **kw: 0.0)
    main.history = History(tmp_path / "history.db")
    main.sessions = SessionStore(tmp_path / "history.db", ttl_days=60)

    provider = RecordingProvider()
    monkeypatch.setattr(main, "get_provider", lambda name: provider)
    return TestClient(main.app), provider, doc_id


def _wait(client, job_id):
    for _ in range(100):
        snap = client.get(f"/api/answer/{job_id}").json()
        if snap["status"] != "running":
            return snap
        time.sleep(0.02)
    return snap


def test_quick_summarize_page(env):
    client, provider, doc_id = env
    resp = client.post("/api/quick", json={"action": "summarize_page"})
    assert resp.status_code == 200
    snap = _wait(client, resp.json()["job_id"])
    assert snap["status"] == "done"
    assert snap["question_read"].startswith("Summarize page 1")
    assert "Thermodynamics" in provider.calls[-1]["prompt"]


def test_quick_highlight_actions_without_highlights(env):
    client, _, _ = env
    for action in ("explain_highlights", "define_highlight"):
        resp = client.post("/api/quick", json={"action": action})
        assert resp.status_code == 400


def test_ask_with_page_image(env):
    client, provider, doc_id = env
    resp = client.post(
        "/api/ask",
        json={
            "strokes": [[[1, 1], [2, 2]]],
            "canvas_w": 100,
            "canvas_h": 100,
            "include_doc_text": "image",
        },
    )
    assert resp.status_code == 200
    snap = _wait(client, resp.json()["job_id"])
    assert snap["status"] == "done"
    images = provider.calls[-1]["images"]
    assert images and images[0].endswith("-0.png")


def test_export_push_uses_scp(env, monkeypatch):
    client, _, doc_id = env
    from app import export as export_mod

    pushed = {}
    monkeypatch.setattr(
        export_mod, "push_to_tablet", lambda pdf, name: pushed.update(name=name, size=len(pdf)) or "new-uuid"
    )
    from app import main

    monkeypatch.setattr(main.export, "push_to_tablet", export_mod.push_to_tablet)

    resp = client.post("/api/ask", json={"strokes": [[[1, 1], [2, 2]]], "canvas_w": 100, "canvas_h": 100})
    _wait(client, resp.json()["job_id"])

    resp = client.post(f"/api/export/{doc_id}?push=true")
    assert resp.status_code == 200
    assert resp.json()["pushed"] is True
    assert resp.json()["tablet_doc_id"] == "new-uuid"
    assert pushed["name"] == "Notes - Paper"
    assert pushed["size"] > 500
