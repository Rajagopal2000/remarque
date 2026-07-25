import time

import pytest
from fastapi.testclient import TestClient

from app.export import _safe_name, build_notes_markdown


def test_build_notes_markdown_structure():
    turns = [
        {"role": "user", "content": "What is entropy?", "ts": 1700000000.0},
        {"role": "assistant", "content": "A measure of disorder.", "ts": 1700000060.0},
    ]
    highlights = [{"page_index": 3, "texts": ["the Carnot limit"]}]
    md = build_notes_markdown("My Paper", turns, highlights)
    assert md.startswith("---\n")
    assert 'title: "Reading notes: My Paper"' in md
    assert "# My Paper" in md
    assert "## Highlights" in md
    assert "- (p. 4) the Carnot limit" in md
    assert "**Q: What is entropy?**" in md
    assert "A measure of disorder." in md


def test_safe_name_strips_path_hostile_characters():
    assert _safe_name('A/B: "C"?') == "A-B- -C"
    assert _safe_name("///") == "untitled"


@pytest.fixture
def env(monkeypatch, tmp_path):
    from app import main
    from app.config import settings
    from app.history import History
    from app.sessions import SessionStore

    monkeypatch.setattr(settings, "sync_dir", tmp_path / "sync")
    settings.sync_dir.mkdir()
    monkeypatch.setattr(settings, "obsidian_dir", str(tmp_path / "vault" / "Remarque"))
    monkeypatch.setattr(main, "run_sync", lambda **kw: 0.0)
    main.history = History(tmp_path / "history.db")
    main.sessions = SessionStore(tmp_path / "history.db", ttl_days=60)

    class P:
        name = "fake"
        supports_sessions = True

        def transcribe(self, ink_png):
            return "what is entropy?"

        def answer_events(self, prompt, system, resume, images=None):
            yield ("text", "Disorder.")
            yield ("session", "s1")

    monkeypatch.setattr(main, "get_provider", lambda name: P())
    return TestClient(main.app), tmp_path


def _ask(client):
    job_id = client.post(
        "/api/ask", json={"strokes": [[[1, 1], [2, 2]]], "canvas_w": 100, "canvas_h": 100}
    ).json()["job_id"]
    for _ in range(200):
        if client.get(f"/api/answer/{job_id}").json()["status"] != "running":
            break
        time.sleep(0.02)


def test_export_writes_markdown_into_vault(env):
    client, tmp_path = env
    _ask(client)
    resp = client.post("/api/export/__no_document__?push=false")
    assert resp.status_code == 200
    path = resp.json()["obsidian_path"]
    assert path.endswith("General questions.md")
    content = (tmp_path / "vault" / "Remarque" / "General questions.md").read_text()
    assert "**Q: what is entropy?**" in content
    assert "Disorder." in content


def test_markdown_download_endpoint(env):
    client, _ = env
    _ask(client)
    resp = client.get("/api/export/__no_document__.md")
    assert resp.status_code == 200
    assert "**Q: what is entropy?**" in resp.text
    assert client.get("/api/export/unknown.md").status_code == 404


def test_export_survives_unwritable_vault(env, monkeypatch, tmp_path):
    from app.config import settings

    client, _ = env
    blocker = tmp_path / "blocked"
    blocker.write_text("a file, not a dir")
    monkeypatch.setattr(settings, "obsidian_dir", str(blocker / "sub"))
    _ask(client)
    resp = client.post("/api/export/__no_document__?push=false")
    assert resp.status_code == 200
    assert "obsidian_error" in resp.json()
    assert resp.json()["pdf_bytes"] > 500
