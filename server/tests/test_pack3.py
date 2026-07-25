"""Feature pack 3: history pages, weak-spot quiz, transcription rescue,
session warm-up, compaction, and the reading digest."""

import json
import time
import uuid

import pymupdf
import pytest
from fastapi.testclient import TestClient

from app.quizbank import QuizResultStore, parse_verdict


class Provider:
    """Session provider with scripted answers; records prompts and transcribe calls."""

    name = "fake"
    supports_sessions = True

    def __init__(self, outputs=None):
        self.outputs = list(outputs or [])
        self.calls = []
        self.transcribed = []
        self.session_n = 0

    def transcribe(self, ink_png, strong=False):
        self.transcribed.append(strong)
        return "strong read" if strong else "weak read"

    def answer_events(self, prompt, system, resume_session_id, images=None):
        self.calls.append({"prompt": prompt, "resume": resume_session_id})
        yield ("text", self.outputs.pop(0) if self.outputs else "Answer.")
        self.session_n += 1
        yield ("session", f"sess-{self.session_n}")


@pytest.fixture
def env(monkeypatch, tmp_path):
    from app import main, marginalia, quizbank
    from app.anki import AnkiStateStore
    from app.config import settings
    from app.history import History
    from app.search import SearchIndex
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

    monkeypatch.setattr(settings, "sync_dir", tmp_path)
    monkeypatch.setattr(main, "run_sync", lambda **kw: 0.0)
    db = tmp_path / "history.db"
    main.history = History(db)
    main.sessions = SessionStore(db, ttl_days=60)
    main.margin_notes = marginalia.MarginNoteStore(db)
    main.search_index = SearchIndex(db)
    main.quiz_results = quizbank.QuizResultStore(db)
    main.anki_state = AnkiStateStore(db)
    main.quiz_pending.clear()

    provider = Provider()
    monkeypatch.setattr(main, "get_provider", lambda name: provider)
    return TestClient(main.app), provider, main, doc_id


INK = {"strokes": [[[1, 1], [2, 2]]], "canvas_w": 100, "canvas_h": 100}


def _wait(client, job_id):
    for _ in range(300):
        snap = client.get(f"/api/answer/{job_id}").json()
        if snap["status"] != "running":
            return snap
        time.sleep(0.02)
    return snap


def _wait_warm(main, doc_id, provider):
    for _ in range(300):
        if main.sessions.get(doc_id, provider.name) is not None:
            return
        time.sleep(0.02)
    raise AssertionError("warm-up never completed")


# -- history pages --


def test_ask_records_page_number(env):
    client, provider, main, doc_id = env
    snap = _wait(client, client.post("/api/ask", json=INK).json()["job_id"])
    assert snap["status"] == "done"
    turns = client.get(f"/api/history/{doc_id}").json()["turns"]
    assert turns[0]["page"] == 1
    md = client.get(f"/api/export/{doc_id}.md").text
    assert "*(p. 1)*" in md


# -- weak-spot quizzing --


def test_parse_verdict():
    assert parse_verdict("Correct. Nice work.") == "correct"
    assert parse_verdict("Partially correct - you missed Tc.") == "partial"
    assert parse_verdict("  incorrect: that is backwards") == "incorrect"
    assert parse_verdict("Hmm, hard to say") == "unknown"
    # Models decorate the verdict despite the prompt; formatting must not
    # turn a mastered question into a permanent weak spot.
    assert parse_verdict("**Correct** - the Carnot limit.") == "correct"
    assert parse_verdict("Verdict: Partially correct.") == "partial"
    assert parse_verdict('"Incorrect." It is 1 - Tc/Th.') == "incorrect"


def test_weak_spots_latest_verdict_wins(tmp_path):
    store = QuizResultStore(tmp_path / "q.db")
    store.add("d", "Q1", "incorrect")
    store.add("d", "Q2", "partial")
    store.add("d", "Q1", "correct")  # later mastered: drops out
    store.add("d", "Q3", "correct")
    assert store.weak_spots("d") == ["Q2"]


def test_quiz_steers_toward_missed_questions(env):
    client, provider, main, doc_id = env
    provider.outputs = ["What is the Carnot limit?", "Incorrect. It is 1 - Tc/Th."]
    _wait(client, client.post("/api/quiz", json={}).json()["job_id"])
    _wait(client, client.post("/api/quiz/answer", json=INK).json()["job_id"])
    assert main.quiz_results.weak_spots(doc_id) == ["What is the Carnot limit?"]

    provider.outputs = ["Rephrased Carnot question?"]
    _wait(client, client.post("/api/quiz", json={}).json()["job_id"])
    prompt = provider.calls[-1]["prompt"]
    assert "wrong or only partially" in prompt
    assert "What is the Carnot limit?" in prompt


# -- transcription rescue --


def test_strong_transcribe_flag_reaches_provider(env):
    client, provider, main, doc_id = env
    _wait(client, client.post("/api/ask", json=INK).json()["job_id"])
    snap = _wait(
        client, client.post("/api/ask", json={**INK, "strong_transcribe": True}).json()["job_id"]
    )
    assert provider.transcribed == [False, True]
    assert snap["question_read"] == "strong read"


# -- session warm-up --


def test_refresh_warms_session_and_ask_resumes_it(env):
    client, provider, main, doc_id = env
    resp = client.post("/api/refresh")
    assert resp.status_code == 200
    _wait_warm(main, doc_id, provider)
    warm_call = provider.calls[0]
    assert "This session is a conversation" in warm_call["prompt"]
    assert "No question yet" in warm_call["prompt"]

    snap = _wait(client, client.post("/api/ask", json=INK).json()["job_id"])
    assert snap["status"] == "done"
    # The ask resumed the warmed session instead of seeding again.
    assert provider.calls[-1]["resume"] == "sess-1"
    assert "This session is a conversation" not in provider.calls[-1]["prompt"]

    # A second refresh does not warm again.
    client.post("/api/refresh")
    time.sleep(0.1)
    assert len([c for c in provider.calls if "No question yet" in c["prompt"]]) == 1


def test_refresh_degrades_to_cached_data_when_sync_fails(env, monkeypatch):
    from app import main

    client, provider, main_mod, doc_id = env

    def boom(**kw):
        raise RuntimeError("tablet unreachable")

    monkeypatch.setattr(main, "run_sync", boom)
    resp = client.post("/api/refresh")
    assert resp.status_code == 200
    assert resp.json()["doc_id"] == doc_id
    assert "tablet unreachable" in resp.json()["sync_error"]


# -- session compaction --


def test_compact_session_summarizes_and_reseeds(env):
    client, provider, main, doc_id = env
    _wait(client, client.post("/api/ask", json=INK).json()["job_id"])
    old = main.sessions.get(doc_id, "fake")
    assert old is not None

    provider.outputs = ["Dense summary of our chat.", "Ready."]
    snap = _wait(client, client.post(f"/api/session/{doc_id}/compact").json()["job_id"])
    assert snap["status"] == "done"
    assert "compacted" in snap["text_so_far"]
    # Summary was requested from the OLD session.
    summary_call = provider.calls[-2]
    assert summary_call["resume"] == old.session_id
    assert "Summarize everything important" in summary_call["prompt"]
    # The fresh session was seeded with document text plus the summary.
    seed_call = provider.calls[-1]
    assert seed_call["resume"] is None
    assert "Dense summary of our chat." in seed_call["prompt"]
    assert "Carnot limit" in seed_call["prompt"]
    fresh = main.sessions.get(doc_id, "fake")
    assert fresh.session_id != old.session_id


def test_compact_without_session_is_404(env):
    client, _, _, doc_id = env
    assert client.post(f"/api/session/{doc_id}/compact").status_code == 404


def test_compact_reseed_failure_keeps_old_session(env, monkeypatch):
    client, provider, main, doc_id = env
    _wait(client, client.post("/api/ask", json=INK).json()["job_id"])
    old = main.sessions.get(doc_id, "fake")
    assert old is not None

    # Summary succeeds (resumes the old session); the reseed (resume=None) fails.
    provider.outputs = ["Dense summary of our chat."]
    real = provider.answer_events

    def flaky(prompt, system, resume, images=None):
        if resume is None:
            raise RuntimeError("provider down")
        return real(prompt, system, resume, images)

    monkeypatch.setattr(provider, "answer_events", flaky)
    snap = _wait(client, client.post(f"/api/session/{doc_id}/compact").json()["job_id"])
    assert snap["status"] == "error"
    assert main.sessions.get(doc_id, "fake").session_id == old.session_id


# -- reading digest --


def test_digest_markdown_contents(env, monkeypatch, tmp_path):
    from app.config import settings

    client, provider, main, doc_id = env
    monkeypatch.setattr(settings, "obsidian_dir", str(tmp_path / "vault"))

    _wait(client, client.post("/api/ask", json=INK).json()["job_id"])
    provider.outputs = ["What is the Carnot limit?", "Partially correct. Almost."]
    _wait(client, client.post("/api/quiz", json={}).json()["job_id"])
    _wait(client, client.post("/api/quiz/answer", json=INK).json()["job_id"])
    main.margin_notes.save(doc_id, "p1", "hash", "check eq 3")

    resp = client.post("/api/digest?days=7")
    assert resp.status_code == 200
    md = resp.json()["markdown"]
    assert "# Reading digest" in md
    assert "- [[Paper]]" in md
    assert "weak read (p. 1)" in md
    assert "1 partially correct" in md
    assert "[Paper] What is the Carnot limit? (partially correct)" in md
    assert "[Paper] check eq 3" in md
    path = resp.json()["obsidian_path"]
    assert "Remarque digest" in path
    assert (tmp_path / "vault").exists()

    assert client.post("/api/digest?days=0").status_code == 400


def test_digest_fractional_days_keeps_recent_activity(env):
    # DIGEST_EVERY_DAYS=0.5 passes the > 0 enablement check; the window must
    # not truncate to zero days (which would report an always-empty digest).
    client, provider, main, doc_id = env
    _wait(client, client.post("/api/ask", json=INK).json()["job_id"])
    md = main._digest_markdown(0.5)
    assert "- [[Paper]]" in md
    assert "weak read" in md
