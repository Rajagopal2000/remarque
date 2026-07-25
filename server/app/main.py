"""FastAPI companion service for the reMarkable AI reading assistant.

Ask flow: cheap model transcribes the handwriting; the strong model answers
inside a persistent per-document session (seeded once with the document text).
"""

import logging
import threading
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Response
from pydantic import BaseModel, Field

from . import (
    anki,
    digest,
    documents,
    export,
    marginalia,
    metrics,
    pdftext,
    prompting,
    quizbank,
    search,
    sync,
)
from .config import settings
from .highlights import extract_highlights
from .history import History
from .inkrender import render_strokes
from .jobs import Job, store
from .providers import get_provider
from .sessions import SessionStore
from .sync import run_sync

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("remarque")

def require_token(x_api_token: str | None = Header(default=None)) -> None:
    if settings.api_token and x_api_token != settings.api_token:
        raise HTTPException(status_code=401, detail="invalid or missing X-Api-Token")


@asynccontextmanager
async def _lifespan(app: FastAPI):
    if settings.anki_auto_hours > 0:
        threading.Thread(target=_anki_auto_loop, daemon=True, name="anki-auto").start()
        log.info("anki auto-update enabled: every %sh", settings.anki_auto_hours)
    if settings.digest_every_days > 0 and settings.obsidian_dir:
        threading.Thread(target=_digest_loop, daemon=True, name="digest").start()
        log.info("reading digest enabled: every %sd", settings.digest_every_days)
    yield


app = FastAPI(title="reMarkable AI companion", lifespan=_lifespan)
api = APIRouter(prefix="/api", dependencies=[Depends(require_token)])
history = History(settings.history_db)
sessions = SessionStore(settings.history_db, settings.session_ttl_days)
anki_state = anki.AnkiStateStore(settings.history_db)
margin_notes = marginalia.MarginNoteStore(settings.history_db)
search_index = search.SearchIndex(settings.history_db)
quiz_results = quizbank.QuizResultStore(settings.history_db)

NO_DOC = "__no_document__"


def _answer_provider():
    return get_provider(settings.provider)


def _transcribe_provider():
    return get_provider(settings.transcribe_provider or settings.provider)


def _doc_info(doc: documents.Document) -> dict:
    return {
        "doc_id": doc.doc_id,
        "title": doc.title,
        "page": doc.page_index,
        "n_pages": doc.n_pages,
        "has_pdf": doc.pdf_path is not None,
    }


def _session_info(doc_id: str) -> dict:
    provider = _answer_provider()
    if not provider.supports_sessions:
        return {"supported": False, "provider": provider.name, "exists": False}
    info = sessions.get(doc_id, provider.name)
    return {
        "supported": True,
        "provider": provider.name,
        "exists": info is not None,
        **(info.to_dict(settings.session_ttl_days) if info else {}),
    }


# Documents whose session seed is currently being uploaded in the background.
_warming: set[str] = set()
_warming_lock = threading.Lock()


def _warm_session(doc: documents.Document) -> None:
    """Seed the per-document session in the background so the first ask is fast.

    Triggered when the panel opens (refresh) on a document with no session yet.
    The seed costs the same tokens it would on the first ask; it just happens
    while the user is still writing.
    """
    provider = _answer_provider()
    if not provider.supports_sessions or doc.pdf_path is None:
        return
    if sessions.get(doc.doc_id, provider.name) is not None:
        return
    with _warming_lock:
        if doc.doc_id in _warming:
            return
        _warming.add(doc.doc_id)

    def work() -> None:
        try:
            seed_text = pdftext.full_text(doc.pdf_path, settings.max_doc_chars)
            seed = prompting.build_seed(doc.title, seed_text)
            prompt = f"{seed}\n\nNo question yet. Reply with just: Ready."
            for kind, payload in provider.answer_events(prompt, prompting.ANSWER_SYSTEM, None):
                if kind == "session":
                    sessions.record_use(doc.doc_id, provider.name, str(payload))
            log.info("warmed session for %s", doc.doc_id)
        except Exception as exc:
            log.warning("session warm-up failed for %s: %s", doc.doc_id, exc)
        finally:
            with _warming_lock:
                _warming.discard(doc.doc_id)

    threading.Thread(target=work, daemon=True, name=f"warm-{doc.doc_id[:8]}").start()


def _wait_for_warm(doc_id: str, timeout: float = 90) -> None:
    """Block an ask briefly while this document's warm-up finishes, instead of
    paying for a second seed upload in parallel."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with _warming_lock:
            if doc_id not in _warming:
                return
        time.sleep(0.5)


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


@app.get("/metrics")
def prometheus_metrics() -> Response:
    return Response(content=metrics.generate_latest(), media_type=metrics.CONTENT_TYPE_LATEST)


@api.post("/refresh")
def refresh() -> dict:
    """Force a sync and report the current document.

    A failed sync (tablet asleep or unreachable) degrades to cached data, like
    the ask path does; it only errors when there is no cached document either.
    """
    sync_error = None
    elapsed = 0.0
    try:
        elapsed = run_sync(force=True)
    except Exception as exc:
        metrics.SYNC_FAILURES.inc()
        sync_error = str(exc)
        log.warning("refresh sync failed, using cached data: %s", exc)
    doc = documents.current_document(settings.sync_dir)
    if doc is None:
        if sync_error:
            raise HTTPException(status_code=502, detail=f"sync failed: {sync_error}")
        raise HTTPException(status_code=404, detail="no recently opened document found")
    _warm_session(doc)
    return {
        "sync_seconds": round(elapsed, 2),
        "sync_error": sync_error,
        **_doc_info(doc),
        "session": _session_info(doc.doc_id),
    }


def _sync_and_find_doc(doc_id: str | None) -> documents.Document | None:
    try:
        run_sync()
    except Exception as exc:
        metrics.SYNC_FAILURES.inc()
        log.warning("sync failed, using cached data: %s", exc)
    if doc_id:
        return documents.get_document(settings.sync_dir, doc_id)
    return documents.current_document(settings.sync_dir)


def _current_page_id(doc: documents.Document) -> str | None:
    ids = documents.page_ids(doc.content)
    if 0 <= doc.page_index < len(ids):
        return ids[doc.page_index]
    return None


def _start_answer_job(
    doc: documents.Document | None,
    doc_id: str,
    *,
    kind: str = "ask",
    ink_png: bytes | None = None,
    question: str | None = None,
    page_number: int | None,
    highlights: list[dict] | None,
    extra_text: str | None,
    image_path: str | None = None,
    brief: bool = False,
    margin_page_id: str | None = None,
    transcribe_strong: bool = False,
    wrap_question: Callable[[str], str] | None = None,
    on_success: Callable[[Job], None] | None = None,
) -> str:
    """Common worker: either transcribe ink into a question, or use a given one.

    wrap_question maps the displayed question (job.question_read) to the text the
    model is actually asked; on_success runs after a completed, uncancelled job
    (quiz mode uses both).
    """
    answer_provider = _answer_provider()
    transcribe_provider = _transcribe_provider()
    metrics.ASKS.labels(kind).inc()

    def work(job: Job) -> None:
        try:
            if ink_png is not None:
                job.phase = "transcribing"
                started = time.monotonic()
                if transcribe_strong:
                    job.question_read = transcribe_provider.transcribe(ink_png, strong=True)
                else:
                    job.question_read = transcribe_provider.transcribe(ink_png)
                metrics.TRANSCRIBE_SECONDS.observe(time.monotonic() - started)
            else:
                job.question_read = question or ""
            if job.cancelled:
                return
            margin_note = None
            if margin_page_id is not None:
                # Cached by ink hash: costs a vision call only when the page ink changed.
                try:
                    margin_note = marginalia.page_note(
                        margin_notes, transcribe_provider, settings.sync_dir, doc_id, margin_page_id
                    )
                except Exception as exc:
                    log.warning("margin note transcription failed: %s", exc)
            job.phase = "answering"
            answer_started = time.monotonic()
            ask_question = wrap_question(job.question_read) if wrap_question else job.question_read
            ask_prompt = prompting.build_ask(
                ask_question, page_number, highlights, extra_text, brief=brief,
                margin_note=margin_note,
            )

            resume_id = None
            if answer_provider.supports_sessions:
                _wait_for_warm(doc_id)
                existing = sessions.get(doc_id, answer_provider.name)
                if existing is not None:
                    resume_id = existing.session_id
                    prompt = ask_prompt
                else:
                    seed_text = None
                    if doc is not None and doc.pdf_path is not None:
                        seed_text = pdftext.full_text(doc.pdf_path, settings.max_doc_chars)
                    seed = prompting.build_seed(doc.title if doc else None, seed_text)
                    prompt = f"{seed}\n\n{ask_prompt}"
            else:
                parts = []
                if doc is not None and doc.pdf_path is not None and extra_text is None:
                    parts.append(
                        "Document text:\n<document>\n"
                        + pdftext.full_text(doc.pdf_path, settings.max_doc_chars)
                        + "\n</document>"
                    )
                history_text = prompting.build_stateless_history(history.recent(doc_id))
                if history_text:
                    parts.append(history_text)
                parts.append(ask_prompt)
                prompt = "\n\n".join(parts)

            images = [image_path] if image_path else None
            for event_kind, payload in answer_provider.answer_events(
                prompt, prompting.ANSWER_SYSTEM, resume_id, images
            ):
                if job.cancelled:
                    break
                if event_kind == "text":
                    job.append(str(payload))
                elif event_kind == "session":
                    sessions.record_use(doc_id, answer_provider.name, str(payload))
                    job.session = _session_info(doc_id)
                elif event_kind == "usage":
                    job.usage = payload  # type: ignore[assignment]
                    log.info("ask usage doc=%s %s", doc_id, payload)
                    if isinstance(payload, dict):
                        for direction, key in (
                            ("input", "input_tokens"),
                            ("cached", "cached_input_tokens"),
                            ("output", "output_tokens"),
                        ):
                            metrics.TOKENS.labels(answer_provider.name, direction).inc(
                                payload.get(key) or 0
                            )

            metrics.ANSWER_SECONDS.observe(time.monotonic() - answer_started)
            if job.cancelled:
                metrics.CANCELLED.labels(kind).inc()
                return
            history.add(doc_id, "user", job.question_read, page=page_number)
            history.add(doc_id, "assistant", job.text.strip(), page=page_number)
            if on_success is not None:
                on_success(job)
        except Exception:
            metrics.ERRORS.labels(kind).inc()
            raise
        finally:
            if image_path:
                Path(image_path).unlink(missing_ok=True)

    return store.start(work)


class AskRequest(BaseModel):
    strokes: list[list[list[float]]]
    canvas_w: float
    canvas_h: float
    include_highlights: bool = True
    # Extra per-ask context on top of the session seed:
    # none | page (text) | full (text) | image (render current page for vision)
    include_doc_text: str = Field(default="none", pattern="^(none|page|full|image)$")
    brief: bool = False
    doc_id: str | None = None
    # Re-read the ink with the answer model (rescue for a bad transcription).
    strong_transcribe: bool = False


@api.post("/ask")
def ask(req: AskRequest) -> dict:
    if not req.strokes or not any(req.strokes):
        raise HTTPException(status_code=400, detail="no ink strokes provided")

    doc = _sync_and_find_doc(req.doc_id)
    doc_id = doc.doc_id if doc else NO_DOC

    page_number = None
    extra_text = None
    image_path = None
    doc_highlights = None
    if doc is not None:
        pdf_page = documents.pdf_page_index(doc.content, doc.page_index)
        if pdf_page >= 0:
            page_number = pdf_page + 1
        if doc.pdf_path is not None and pdf_page >= 0:
            if req.include_doc_text == "full":
                extra_text = pdftext.full_text(doc.pdf_path, settings.max_doc_chars)
            elif req.include_doc_text == "page":
                extra_text = pdftext.page_window(doc.pdf_path, pdf_page)
            elif req.include_doc_text == "image":
                out = settings.agent_home / "page-images" / f"{doc.doc_id}-{pdf_page}.png"
                image_path = str(pdftext.render_page_png(doc.pdf_path, pdf_page, out))
        if req.include_highlights:
            doc_highlights = extract_highlights(settings.sync_dir, doc.doc_id, doc.content)

    ink_png = render_strokes(req.strokes, req.canvas_w, req.canvas_h)
    job_id = _start_answer_job(
        doc,
        doc_id,
        kind="ask",
        ink_png=ink_png,
        page_number=page_number,
        highlights=doc_highlights,
        extra_text=extra_text,
        image_path=image_path,
        brief=req.brief,
        margin_page_id=_current_page_id(doc) if doc else None,
        transcribe_strong=req.strong_transcribe,
    )
    return {
        "job_id": job_id,
        "doc": _doc_info(doc) if doc else None,
        "session": _session_info(doc_id),
        "sync_age_seconds": sync.last_success_age(),
    }


class QuickRequest(BaseModel):
    action: str = Field(
        pattern="^(summarize_page|summarize_doc|explain_highlights|define_highlight)$"
    )
    brief: bool = False
    doc_id: str | None = None


@api.post("/quick")
def quick(req: QuickRequest) -> dict:
    """One-tap preset questions: no handwriting, no transcription call."""
    doc = _sync_and_find_doc(req.doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="no document found")
    doc_id = doc.doc_id

    pdf_page = documents.pdf_page_index(doc.content, doc.page_index)
    page_number = pdf_page + 1 if pdf_page >= 0 else None
    extra_text = None
    highlights = None

    if req.action == "summarize_page":
        if doc.pdf_path is None or pdf_page < 0:
            raise HTTPException(status_code=400, detail="current page has no PDF text")
        question = f"Summarize page {page_number} of the document concisely."
        extra_text = pdftext.page_window(doc.pdf_path, pdf_page, radius=0)
    elif req.action == "summarize_doc":
        question = (
            "Give a concise structured summary of the document: "
            "its goal, approach, key findings, and limitations."
        )
    else:
        highlights = extract_highlights(settings.sync_dir, doc_id, doc.content)
        if not highlights:
            raise HTTPException(status_code=400, detail="no highlights found in this document")
        if req.action == "explain_highlights":
            question = "Explain the highlighted passages in the context of the document."
        else:  # define_highlight
            term = highlights[-1]["texts"][-1]
            question = f'Define or briefly explain, as used in the document: "{term}"'
            highlights = None  # the term is in the question; no need to list all

    job_id = _start_answer_job(
        doc,
        doc_id,
        kind="quick",
        question=question,
        page_number=page_number,
        highlights=highlights,
        extra_text=extra_text,
        brief=req.brief,
        margin_page_id=_current_page_id(doc),
    )
    return {
        "job_id": job_id,
        "doc": _doc_info(doc),
        "session": _session_info(doc_id),
        "sync_age_seconds": sync.last_success_age(),
    }


# Pending quiz question per document (in-memory: a lost question just means
# tapping Quiz again after a server restart).
quiz_pending: dict[str, str] = {}


class QuizStartRequest(BaseModel):
    doc_id: str | None = None


@api.post("/quiz")
def quiz_start(req: QuizStartRequest) -> dict:
    """Generate one exam-style question about the document (async job)."""
    doc = _sync_and_find_doc(req.doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="no document found")
    doc_id = doc.doc_id

    def remember_question(job: Job) -> None:
        quiz_pending[doc_id] = job.text.strip()

    weak = quiz_results.weak_spots(doc_id)
    job_id = _start_answer_job(
        doc,
        doc_id,
        kind="quiz",
        question="Quiz me",
        page_number=None,
        highlights=None,
        extra_text=None,
        wrap_question=lambda _: prompting.build_quiz_question(weak),
        on_success=remember_question,
    )
    return {
        "job_id": job_id,
        "doc": _doc_info(doc),
        "session": _session_info(doc_id),
        "sync_age_seconds": sync.last_success_age(),
    }


class QuizAnswerRequest(BaseModel):
    strokes: list[list[list[float]]]
    canvas_w: float
    canvas_h: float
    doc_id: str | None = None


@api.post("/quiz/answer")
def quiz_answer(req: QuizAnswerRequest) -> dict:
    """Transcribe the handwritten answer and grade it against the pending question."""
    if not req.strokes or not any(req.strokes):
        raise HTTPException(status_code=400, detail="no ink strokes provided")
    doc = _sync_and_find_doc(req.doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="no document found")
    doc_id = doc.doc_id
    question = quiz_pending.get(doc_id)
    if question is None:
        raise HTTPException(status_code=409, detail="no quiz question pending; tap Quiz first")

    def record_result(job: Job) -> None:
        quiz_pending.pop(doc_id, None)
        quiz_results.add(doc_id, question, quizbank.parse_verdict(job.text))

    ink_png = render_strokes(req.strokes, req.canvas_w, req.canvas_h)
    job_id = _start_answer_job(
        doc,
        doc_id,
        kind="quiz_grade",
        ink_png=ink_png,
        page_number=None,
        highlights=None,
        extra_text=None,
        wrap_question=lambda answer: prompting.build_quiz_grade(question, answer),
        on_success=record_result,
    )
    return {
        "job_id": job_id,
        "doc": _doc_info(doc),
        "session": _session_info(doc_id),
        "sync_age_seconds": sync.last_success_age(),
    }


class SearchRequest(BaseModel):
    q: str | None = None
    strokes: list[list[list[float]]] | None = None
    canvas_w: float | None = None
    canvas_h: float | None = None


@api.post("/search")
def search_all(req: SearchRequest) -> dict:
    """Search history and document text; the query is typed (q) or handwritten."""
    query = (req.q or "").strip()
    if not query:
        if not req.strokes or not any(req.strokes):
            raise HTTPException(status_code=400, detail="provide q or ink strokes")
        try:
            ink_png = render_strokes(req.strokes, req.canvas_w or 0, req.canvas_h or 0)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        query = _transcribe_provider().transcribe(ink_png).strip()
    try:
        run_sync()
    except Exception as exc:
        metrics.SYNC_FAILURES.inc()
        log.warning("sync failed, searching cached data: %s", exc)
    docs = documents.list_documents(settings.sync_dir)
    return {"query": query, **search_index.search(query, docs)}


@api.post("/export/{doc_id}")
def export_notes(doc_id: str, push: bool = True) -> dict:
    turns = history.recent(doc_id, limit=500)
    if not turns:
        raise HTTPException(status_code=404, detail="no history for this document")
    doc = documents.get_document(settings.sync_dir, doc_id)
    title = doc.title if doc else "General questions"
    pdf_bytes = export.build_notes_pdf(title, turns)
    result = {"doc_id": doc_id, "title": title, "pdf_bytes": len(pdf_bytes), "pushed": False}
    if settings.obsidian_dir:
        highlights = extract_highlights(settings.sync_dir, doc_id, doc.content) if doc else None
        markdown = export.build_notes_markdown(title, turns, highlights)
        # An unreachable vault must not block the tablet push; report it instead.
        try:
            result["obsidian_path"] = str(export.write_to_obsidian(title, markdown))
        except OSError as exc:
            result["obsidian_error"] = str(exc)
    if push:
        try:
            new_id = export.push_to_tablet(pdf_bytes, f"Notes - {title}")
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"push failed: {exc}")
        result.update(
            pushed=True,
            tablet_doc_id=new_id,
            note="restart the tablet (or xochitl) to see the notes document",
        )
    return result


# A manual build and an auto-update racing on the same deck would each generate
# cards for the same new content and the last state save would win.
_anki_build_lock = threading.Lock()


def _build_anki_deck(job: Job, doc: documents.Document | None, doc_id: str) -> None:
    """Generate or incrementally update one document's deck; fills the job.

    Shared by the /api/anki route (async job) and the auto-update scheduler.
    """
    with _anki_build_lock:
        state = anki_state.get(doc_id)
        provider = _answer_provider()
        transcriber = _transcribe_provider()
        turns = history.recent(doc_id, limit=500)
        title = doc.title if doc else "General questions"
        doc_text = (
            pdftext.full_text(doc.pdf_path, settings.max_doc_chars)
            if doc is not None and doc.pdf_path is not None
            else None
        )
        highlight_texts: list[str] = []
        if doc is not None:
            for h in extract_highlights(settings.sync_dir, doc_id, doc.content):
                highlight_texts.extend(h["texts"])

        job.phase = "answering"
        job.question_read = f"Anki deck for: {title}"
        max_ts = max((t["ts"] for t in turns), default=0.0)
        margin_texts: list[str] = []
        if doc is not None:
            margin_texts = [
                n["text"]
                for n in marginalia.doc_notes(
                    margin_notes, transcriber, settings.sync_dir, doc_id, doc.content
                )
            ]

        if state is None:
            new_cards = anki.generate_cards(
                provider, title, doc_text, turns,
                highlights=highlight_texts, margin_notes=margin_texts,
            )
            all_cards = new_cards
            verb = "Created"
        else:
            new_turns = [t for t in turns if t["ts"] > state.last_history_ts]
            known = set(state.highlight_texts)
            new_highlights = [h for h in highlight_texts if h not in known]
            if state.margin_texts is None:
                # Deck predates margin tracking: treat current notes as already
                # covered instead of regenerating cards for every old note.
                new_margin = []
            else:
                known_margin = set(state.margin_texts)
                new_margin = [m for m in margin_texts if m not in known_margin]
            doc_changed = anki.text_hash(doc_text) != state.doc_text_hash
            if not new_turns and not new_highlights and not new_margin and not doc_changed:
                job.append(
                    f"No new content since the last deck ({len(state.cards)} cards). "
                    "Nothing to add."
                )
                return
            new_cards = anki.generate_cards(
                provider,
                title,
                doc_text if doc_changed else None,
                new_turns,
                highlights=new_highlights,
                margin_notes=new_margin,
                existing_fronts=[anki.card_front(c) for c in state.cards],
                update_only=True,
            )
            all_cards = state.cards + new_cards
            verb = "Added"
            if not new_cards:
                job.append(
                    f"New content reviewed, but it was already covered "
                    f"({len(state.cards)} cards unchanged)."
                )
                return

        if not all_cards:
            raise RuntimeError("no cards could be generated")
        counts = {
            t: sum(1 for c in new_cards if c["type"] == t) for t in ("basic", "reversed", "cloze")
        }
        apkg = anki.build_apkg(title, all_cards)
        out = settings.agent_home.parent / "anki" / f"{doc_id}.apkg"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(apkg)
        anki_state.save(
            doc_id,
            anki.DeckState(
                cards=all_cards,
                last_history_ts=max_ts,
                highlight_texts=highlight_texts,
                doc_text_hash=anki.text_hash(doc_text),
                margin_texts=margin_texts,
            ),
        )
        summary = (
            f"{verb} {len(new_cards)} cards ({counts['basic']} basic, "
            f"{counts['reversed']} reversed, {counts['cloze']} cloze); deck total {len(all_cards)}."
        )
        if settings.anki_connect_url:
            added = anki.push_via_ankiconnect(title, new_cards)
            summary += f" Pushed {added} new notes to Anki and synced to AnkiWeb."
        else:
            summary += f" Download at /api/anki/{doc_id}.apkg"
        job.append(summary)


@api.post("/anki/{doc_id}")
def create_anki_deck(doc_id: str, mode: str = "auto") -> dict:
    """Generate or incrementally update the Anki deck for a document (async job).

    mode=auto: first run covers everything; later runs add cards only for new
    material (new chat, new highlights, new margin notes, changed document
    text). mode=full discards the tracking state and regenerates from scratch.
    """
    if mode not in ("auto", "full"):
        raise HTTPException(status_code=400, detail="mode must be auto or full")
    doc = documents.get_document(settings.sync_dir, doc_id)
    if doc is None and doc_id != NO_DOC:
        doc = _sync_and_find_doc(doc_id)
    if doc is None and not history.recent(doc_id, limit=1):
        raise HTTPException(status_code=404, detail="no document or history to make cards from")
    title = doc.title if doc else "General questions"
    if mode == "full":
        anki_state.clear(doc_id)
    state = anki_state.get(doc_id)
    metrics.ASKS.labels("anki").inc()
    job_id = store.start(lambda job: _build_anki_deck(job, doc, doc_id))
    return {"job_id": job_id, "title": title, "incremental": state is not None}


def _anki_auto_tick() -> None:
    """One scheduler pass: incrementally refresh every deck that already exists.

    Having a deck is the per-document opt-in (the user pressed Anki once); the
    no-new-content path costs no LLM call, so idle documents are free.
    """
    try:
        run_sync()
    except Exception as exc:
        metrics.SYNC_FAILURES.inc()
        log.warning("anki auto-update: sync failed, using cached data: %s", exc)
    for doc_id in anki_state.doc_ids():
        doc = documents.get_document(settings.sync_dir, doc_id)
        if doc is None and not history.recent(doc_id, limit=1):
            # Same refusal as the /api/anki route: a deck whose document was
            # deleted has no source material to generate from.
            log.info("anki auto-update: skipping %s (no document or history)", doc_id)
            continue
        job = Job(job_id=f"anki-auto-{doc_id[:8]}")
        metrics.ASKS.labels("anki_auto").inc()
        try:
            _build_anki_deck(job, doc, doc_id)
            log.info("anki auto-update %s: %s", doc_id, job.text.strip())
        except Exception as exc:
            metrics.ERRORS.labels("anki_auto").inc()
            log.warning("anki auto-update failed for %s: %s", doc_id, exc)


def _scheduler_loop(name: str, interval_s: float, tick: Callable[[], None]) -> None:
    """Run tick every interval_s, tracked by a stamp file so a restart resumes
    the schedule (an overdue tick fires immediately) instead of postponing the
    next tick by a full interval every time the process comes up."""
    stamp = settings.agent_home / f"{name}.last-run"
    while True:
        last = stamp.stat().st_mtime if stamp.exists() else 0.0
        wait = last + interval_s - time.time()
        if wait > 0:
            time.sleep(wait)
        stamp.touch()
        try:
            tick()
        except Exception:
            log.exception("%s tick failed", name)


def _anki_auto_loop() -> None:
    _scheduler_loop("anki-auto", settings.anki_auto_hours * 3600, _anki_auto_tick)


def _digest_markdown(days: float) -> str:
    now = time.time()
    since = now - days * 86400
    docs = documents.list_documents(settings.sync_dir)
    titles = {d.doc_id: d.title for d in docs}
    opened = [d.title for d in docs if d.last_opened_ms / 1000 >= since]
    return digest.build_digest_markdown(
        days=days,
        now=now,
        docs_opened=opened,
        turns=history.since(since),
        quiz_results=quiz_results.since(since),
        margin_notes=margin_notes.updated_since(since),
        anki_updates=anki_state.updated_since(since),
        titles=titles,
    )


@api.post("/digest")
def create_digest(days: int = 7, write: bool = True) -> dict:
    """Build the reading digest; with OBSIDIAN_DIR set, also write it to the vault."""
    if days < 1:
        raise HTTPException(status_code=400, detail="days must be >= 1")
    markdown = _digest_markdown(days)
    result: dict = {"days": days, "markdown": markdown}
    if write and settings.obsidian_dir:
        stamp = time.strftime("%Y-%m-%d")
        try:
            result["obsidian_path"] = str(
                export.write_to_obsidian(f"Remarque digest {stamp}", markdown)
            )
        except OSError as exc:
            result["obsidian_error"] = str(exc)
    return result


def _digest_tick() -> None:
    path = export.write_to_obsidian(
        f"Remarque digest {time.strftime('%Y-%m-%d')}",
        _digest_markdown(settings.digest_every_days),
    )
    log.info("reading digest written to %s", path)


def _digest_loop() -> None:
    _scheduler_loop("digest", settings.digest_every_days * 86400, _digest_tick)


@api.get("/anki/{doc_id}.apkg")
def download_anki_deck(doc_id: str) -> Response:
    path = settings.agent_home.parent / "anki" / f"{doc_id}.apkg"
    if not path.exists():
        raise HTTPException(status_code=404, detail="no deck generated for this document yet")
    return Response(
        content=path.read_bytes(),
        media_type="application/apkg",
        headers={"Content-Disposition": f'attachment; filename="remarkable-{doc_id}.apkg"'},
    )


@api.get("/export/{doc_id}.pdf")
def download_notes(doc_id: str) -> Response:
    turns = history.recent(doc_id, limit=500)
    if not turns:
        raise HTTPException(status_code=404, detail="no history for this document")
    doc = documents.get_document(settings.sync_dir, doc_id)
    title = doc.title if doc else "General questions"
    return Response(
        content=export.build_notes_pdf(title, turns),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="notes-{doc_id}.pdf"'},
    )


@api.get("/export/{doc_id}.md")
def download_notes_markdown(doc_id: str) -> Response:
    turns = history.recent(doc_id, limit=500)
    if not turns:
        raise HTTPException(status_code=404, detail="no history for this document")
    doc = documents.get_document(settings.sync_dir, doc_id)
    title = doc.title if doc else "General questions"
    highlights = extract_highlights(settings.sync_dir, doc_id, doc.content) if doc else None
    return Response(
        content=export.build_notes_markdown(title, turns, highlights),
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="notes-{doc_id}.md"'},
    )


@api.get("/answer/{job_id}")
def answer(job_id: str, cursor: int = 0) -> dict:
    job = store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown job")
    return job.snapshot(cursor)


@api.post("/answer/{job_id}/cancel")
def cancel_answer(job_id: str) -> dict:
    if not store.cancel(job_id):
        raise HTTPException(status_code=404, detail="unknown job")
    return store.get(job_id).snapshot(0)


@api.get("/session/{doc_id}")
def get_session(doc_id: str) -> dict:
    return {"doc_id": doc_id, **_session_info(doc_id)}


@api.post("/session/{doc_id}/clear")
def clear_session(doc_id: str) -> dict:
    removed = sessions.clear(doc_id)
    return {"doc_id": doc_id, "cleared": removed, **_session_info(doc_id)}


@api.post("/session/{doc_id}/compact")
def compact_session(doc_id: str) -> dict:
    """Shrink a long-lived session: summarize it, then reseed a fresh one.

    Keeps the 2-year session design viable: the transcript stops growing while
    the accumulated understanding survives as a dense summary.
    """
    provider = _answer_provider()
    if not provider.supports_sessions:
        raise HTTPException(status_code=400, detail="provider has no sessions to compact")
    existing = sessions.get(doc_id, provider.name)
    if existing is None:
        raise HTTPException(status_code=404, detail="no session to compact")
    doc = documents.get_document(settings.sync_dir, doc_id)
    old_turns = existing.turns
    metrics.ASKS.labels("compact").inc()

    def work(job: Job) -> None:
        job.phase = "answering"
        job.question_read = "Compact session"
        summary = "".join(
            str(payload)
            for kind, payload in provider.answer_events(
                prompting.COMPACT_SUMMARY, prompting.ANSWER_SYSTEM, existing.session_id
            )
            if kind == "text"
        ).strip()
        if not summary:
            raise RuntimeError("summary came back empty; session left untouched")
        if job.cancelled:
            return
        seed_text = None
        if doc is not None and doc.pdf_path is not None:
            seed_text = pdftext.full_text(doc.pdf_path, settings.max_doc_chars)
        seed = prompting.build_seed(doc.title if doc else None, seed_text)
        # Reseed first; only a successful reseed may replace the old session.
        new_session_id = None
        for kind, payload in provider.answer_events(
            prompting.build_compact_seed(seed, summary), prompting.ANSWER_SYSTEM, None
        ):
            if kind == "session":
                new_session_id = str(payload)
        if new_session_id is None:
            raise RuntimeError("reseed returned no session; old session left untouched")
        sessions.clear(doc_id)
        sessions.record_use(doc_id, provider.name, new_session_id)
        job.session = _session_info(doc_id)
        job.append(f"Session compacted: {old_turns} turns summarized into a fresh seed.")

    job_id = store.start(work)
    return {"job_id": job_id, "doc_id": doc_id, "turns": old_turns}


@api.get("/history/{doc_id}")
def get_history(doc_id: str) -> dict:
    return {"doc_id": doc_id, "turns": history.recent(doc_id, limit=50)}


@api.get("/debug/current")
def debug_current() -> dict:
    doc = documents.current_document(settings.sync_dir)
    if doc is None:
        raise HTTPException(status_code=404, detail="no document found")
    info = _doc_info(doc)
    if doc.pdf_path is not None:
        pdf_page = documents.pdf_page_index(doc.content, doc.page_index)
        if pdf_page >= 0:
            info["page_text_preview"] = pdftext.page_window(doc.pdf_path, pdf_page, radius=0)[:2000]
    info["highlights"] = extract_highlights(settings.sync_dir, doc.doc_id, doc.content)
    info["session"] = _session_info(doc.doc_id)
    return info

app.include_router(api)
