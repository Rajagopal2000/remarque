import json
import uuid

import pymupdf
import pytest
from fastapi.testclient import TestClient

from app.search import SearchIndex, fts_query


def test_fts_query_sanitizes_punctuation():
    assert fts_query("what is entropy?") == '"what" "is" "entropy"'
    assert fts_query("???") is None
    assert fts_query("") is None


def _make_doc(tmp_path, title, page_texts):
    doc_id = str(uuid.uuid4())
    pdf = pymupdf.open()
    for text in page_texts:
        pdf.new_page().insert_text((72, 72), text)
    pdf.save(tmp_path / f"{doc_id}.pdf")
    (tmp_path / f"{doc_id}.metadata").write_text(
        json.dumps({"type": "DocumentType", "visibleName": title, "lastOpened": "1700000000000"})
    )
    (tmp_path / f"{doc_id}.content").write_text(json.dumps({"pageCount": len(page_texts)}))
    return doc_id


@pytest.fixture
def env(monkeypatch, tmp_path):
    from app import main
    from app.config import settings
    from app.history import History
    from app.search import SearchIndex
    from app.sessions import SessionStore

    doc_id = _make_doc(
        tmp_path, "Thermo Paper", ["Alpha page.", "The Carnot cycle bounds efficiency."]
    )
    monkeypatch.setattr(settings, "sync_dir", tmp_path)
    monkeypatch.setattr(main, "run_sync", lambda **kw: 0.0)
    main.history = History(tmp_path / "history.db")
    main.sessions = SessionStore(tmp_path / "history.db", ttl_days=60)
    main.search_index = SearchIndex(tmp_path / "history.db")

    class T:
        name = "fake"
        supports_sessions = False

        def transcribe(self, ink_png):
            return "carnot efficiency?"

        def answer_events(self, prompt, system, resume, images=None):
            yield ("text", "x")

    monkeypatch.setattr(main, "get_provider", lambda name: T())
    return TestClient(main.app), main, doc_id


def test_search_finds_history_and_documents(env):
    client, main, doc_id = env
    main.history.add(doc_id, "user", "why is the carnot engine not 100% efficient")
    main.history.add(doc_id, "assistant", "Because some heat must be rejected.")

    resp = client.post("/api/search", json={"q": "carnot"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["query"] == "carnot"
    assert any("carnot" in h["snippet"].lower() for h in data["history"])
    assert data["history"][0]["title"] == "Thermo Paper"
    assert data["documents"][0]["page"] == 2
    assert "*Carnot*" in data["documents"][0]["snippet"]


def test_search_by_handwriting_transcribes_first(env):
    client, _, _ = env
    resp = client.post(
        "/api/search",
        json={"strokes": [[[1, 1], [2, 2]]], "canvas_w": 100, "canvas_h": 100},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["query"] == "carnot efficiency?"
    assert data["documents"], "expected a document hit from the transcribed query"


def test_search_requires_query_or_ink(env):
    client, _, _ = env
    assert client.post("/api/search", json={}).status_code == 400
    # Unmatchable punctuation-only query returns empty results, not an error.
    resp = client.post("/api/search", json={"q": "???"})
    assert resp.json() == {"query": "???", "history": [], "documents": []}


def test_index_updates_when_document_changes(env, tmp_path):
    client, main, doc_id = env
    resp = client.post("/api/search", json={"q": "microstates"})
    assert resp.json()["documents"] == []

    pdf_path = tmp_path / f"{doc_id}.pdf"
    pdf = pymupdf.open()
    pdf.new_page().insert_text((72, 72), "Entropy counts microstates.")
    pdf.save(pdf_path)

    resp = client.post("/api/search", json={"q": "microstates"})
    assert resp.json()["documents"][0]["page"] == 1


def test_index_prunes_deleted_documents(env, tmp_path):
    client, main, doc_id = env
    assert client.post("/api/search", json={"q": "carnot"}).json()["documents"]
    for f in tmp_path.glob(f"{doc_id}.*"):
        f.unlink()
    assert client.post("/api/search", json={"q": "carnot"}).json()["documents"] == []
