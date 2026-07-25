"""FastAPI companion service for the reMarkable AI reading assistant.

Ask flow: cheap model transcribes the handwriting; the strong model answers
inside a persistent per-document session (seeded once with the document text).
"""

import logging
import time
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Response
from pydantic import BaseModel, Field

from . import anki, documents, export, metrics, pdftext, prompting, sync
from .config import settings
from .highlights import extract_highlights
from .history import History
from .inkrender import render_strokes
from .jobs import Job, store
from .providers import get_provider
from .sessions import SessionStore
from .sync import SyncError, run_sync

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("remarque")

def require_token(x_api_token: str | None = Header(default=None)) -> None:
    if settings.api_token and x_api_token != settings.api_token:
        raise HTTPException(status_code=401, detail="invalid or missing X-Api-Token")


app = FastAPI(title="reMarkable AI companion")
api = APIRouter(prefix="/api", dependencies=[Depends(require_token)])
history = History(settings.history_db)
sessions = SessionStore(settings.history_db, settings.session_ttl_days)
anki_state = anki.AnkiStateStore(settings.history_db)

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


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


@app.get("/metrics")
def prometheus_metrics() -> Response:
    return Response(content=metrics.generate_latest(), media_type=metrics.CONTENT_TYPE_LATEST)


@api.post("/refresh")
def refresh() -> dict:
    try:
        elapsed = run_sync(force=True)
    except (SyncError, Exception) as exc:
        raise HTTPException(status_code=502, detail=f"sync failed: {exc}")
    doc = documents.current_document(settings.sync_dir)
    if doc is None:
        raise HTTPException(status_code=404, detail="no recently opened document found")
    return {
        "sync_seconds": round(elapsed, 2),
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
) -> str:
    """Common worker: either transcribe ink into a question, or use a given one."""
    answer_provider = _answer_provider()
    transcribe_provider = _transcribe_provider()
    metrics.ASKS.labels(kind).inc()

    def work(job: Job) -> None:
        try:
            if ink_png is not None:
                job.phase = "transcribing"
                started = time.monotonic()
                job.question_read = transcribe_provider.transcribe(ink_png)
                metrics.TRANSCRIBE_SECONDS.observe(time.monotonic() - started)
            else:
                job.question_read = question or ""
            if job.cancelled:
                return
            job.phase = "answering"
            answer_started = time.monotonic()
            ask_prompt = prompting.build_ask(
                job.question_read, page_number, highlights, extra_text, brief=brief
            )

            resume_id = None
            if answer_provider.supports_sessions:
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
            history.add(doc_id, "user", job.question_read)
            history.add(doc_id, "assistant", job.text.strip())
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
    )
    return {
        "job_id": job_id,
        "doc": _doc_info(doc),
        "session": _session_info(doc_id),
        "sync_age_seconds": sync.last_success_age(),
    }


@api.post("/export/{doc_id}")
def export_notes(doc_id: str, push: bool = True) -> dict:
    turns = history.recent(doc_id, limit=500)
    if not turns:
        raise HTTPException(status_code=404, detail="no history for this document")
    doc = documents.get_document(settings.sync_dir, doc_id)
    title = doc.title if doc else "General questions"
    pdf_bytes = export.build_notes_pdf(title, turns)
    result = {"doc_id": doc_id, "title": title, "pdf_bytes": len(pdf_bytes), "pushed": False}
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


@api.post("/anki/{doc_id}")
def create_anki_deck(doc_id: str, mode: str = "auto") -> dict:
    """Generate or incrementally update the Anki deck for a document (async job).

    mode=auto: first run covers everything; later runs add cards only for new
    material (new chat, new highlights, changed document text). mode=full
    discards the tracking state and regenerates from scratch.
    """
    if mode not in ("auto", "full"):
        raise HTTPException(status_code=400, detail="mode must be auto or full")
    doc = documents.get_document(settings.sync_dir, doc_id)
    if doc is None and doc_id != NO_DOC:
        doc = _sync_and_find_doc(doc_id)
    turns = history.recent(doc_id, limit=500)
    if doc is None and not turns:
        raise HTTPException(status_code=404, detail="no document or history to make cards from")
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
    if mode == "full":
        anki_state.clear(doc_id)
    state = anki_state.get(doc_id)
    provider = _answer_provider()
    metrics.ASKS.labels("anki").inc()

    def work(job: Job) -> None:
        job.phase = "answering"
        job.question_read = f"Anki deck for: {title}"
        max_ts = max((t["ts"] for t in turns), default=0.0)

        if state is None:
            new_cards = anki.generate_cards(
                provider, title, doc_text, turns, highlights=highlight_texts
            )
            all_cards = new_cards
            verb = "Created"
        else:
            new_turns = [t for t in turns if t["ts"] > state.last_history_ts]
            known = set(state.highlight_texts)
            new_highlights = [h for h in highlight_texts if h not in known]
            doc_changed = anki.text_hash(doc_text) != state.doc_text_hash
            if not new_turns and not new_highlights and not doc_changed:
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

    job_id = store.start(work)
    return {"job_id": job_id, "title": title, "incremental": state is not None}


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
