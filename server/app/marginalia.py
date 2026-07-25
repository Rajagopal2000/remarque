"""Handwritten margin notes: pen strokes extracted from v6 .rm page files,
rasterized and vision-transcribed so they can join ask context and Anki cards.

Transcriptions are cached per page keyed by an ink hash, so unchanged pages
never cost a vision call. Highlighter and eraser strokes are excluded: the
former are covered by highlight extraction, the latter are not content.
"""

import hashlib
import json
import logging
import sqlite3
import threading
import time
from pathlib import Path

from rmscene import read_blocks
from rmscene import scene_items as si
from rmscene.scene_stream import SceneLineItemBlock

from .documents import page_ids
from .inkrender import render_strokes

log = logging.getLogger(__name__)

_lock = threading.Lock()

_NON_CONTENT_TOOLS = {
    tool.value
    for tool in (si.Pen.HIGHLIGHTER_1, si.Pen.HIGHLIGHTER_2, si.Pen.ERASER, si.Pen.ERASER_AREA)
}


def _page_strokes(rm_path: Path) -> list[list[list[float]]]:
    """Pen polylines ([[x, y], ...] per stroke) from one .rm page file."""
    strokes: list[list[list[float]]] = []
    with rm_path.open("rb") as f:
        for block in read_blocks(f):
            if not isinstance(block, SceneLineItemBlock):
                continue
            line = getattr(block.item, "value", None)
            points = getattr(line, "points", None)
            if not points:
                continue
            tool = getattr(line, "tool", None)
            # rmscene yields the tool as an enum or a raw int depending on version.
            if getattr(tool, "value", tool) in _NON_CONTENT_TOOLS:
                continue
            strokes.append([[float(p.x), float(p.y)] for p in points])
    return strokes


def ink_hash(strokes: list[list[list[float]]]) -> str:
    """Stable digest of the ink: whole-pixel rounding absorbs float noise."""
    data = json.dumps([[[round(x), round(y)] for x, y in stroke] for stroke in strokes])
    return hashlib.md5(data.encode()).hexdigest()


class MarginNoteStore:
    """Per-page cache of margin-note transcriptions keyed by ink hash."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS margin_notes ("
                "doc_id TEXT NOT NULL, page_id TEXT NOT NULL, ink_hash TEXT NOT NULL,"
                "text TEXT NOT NULL, updated_at REAL NOT NULL,"
                "PRIMARY KEY (doc_id, page_id))"
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

    def get(self, doc_id: str, page_id: str) -> tuple[str, str] | None:
        with _lock, self._connect() as conn:
            row = conn.execute(
                "SELECT ink_hash, text FROM margin_notes WHERE doc_id = ? AND page_id = ?",
                (doc_id, page_id),
            ).fetchone()
        return row

    def save(self, doc_id: str, page_id: str, ink_hash_: str, text: str) -> None:
        with _lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO margin_notes VALUES (?, ?, ?, ?, ?)",
                (doc_id, page_id, ink_hash_, text, time.time()),
            )

    def updated_since(self, ts: float) -> list[dict]:
        """Notes (re)transcribed from ts onward (for the reading digest)."""
        with _lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT doc_id, text FROM margin_notes "
                "WHERE updated_at >= ? AND text != '' ORDER BY updated_at",
                (ts,),
            ).fetchall()
        return [{"doc_id": d, "text": t} for d, t in rows]


def page_ink(sync_dir: Path, doc_id: str, page_id: str) -> list[list[list[float]]]:
    """Pen strokes on one page, or [] when the page has no parsable ink."""
    rm_path = sync_dir / doc_id / f"{page_id}.rm"
    if not rm_path.exists():
        return []
    try:
        return _page_strokes(rm_path)
    except Exception as exc:
        log.warning("failed to parse %s: %s", rm_path.name, exc)
        return []


class AskedInkStore:
    """Strokes already consumed as page-ask questions, per page.

    Lets a second question written on the same page be asked alone instead of
    re-reading every older stroke on the page."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS asked_page_ink ("
                "doc_id TEXT NOT NULL, page_id TEXT NOT NULL, stroke_hash TEXT NOT NULL,"
                "PRIMARY KEY (doc_id, page_id, stroke_hash))"
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

    def seen(self, doc_id: str, page_id: str) -> set[str]:
        with _lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT stroke_hash FROM asked_page_ink WHERE doc_id = ? AND page_id = ?",
                (doc_id, page_id),
            ).fetchall()
        return {r[0] for r in rows}

    def add(self, doc_id: str, page_id: str, stroke_hashes: list[str]) -> None:
        with _lock, self._connect() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO asked_page_ink VALUES (?, ?, ?)",
                [(doc_id, page_id, h) for h in stroke_hashes],
            )


def page_note(
    store: MarginNoteStore,
    transcriber,
    sync_dir: Path,
    doc_id: str,
    page_id: str,
) -> str | None:
    """Transcribed margin note for one page, or None if it has no pen ink.

    A vision call happens only when the page's ink changed since the last
    transcription; parsing failures are logged and treated as no note.
    """
    rm_path = sync_dir / doc_id / f"{page_id}.rm"
    if not rm_path.exists():
        return None
    try:
        strokes = _page_strokes(rm_path)
    except Exception as exc:
        log.warning("failed to parse %s: %s", rm_path.name, exc)
        return None
    if not strokes:
        return None
    digest = ink_hash(strokes)
    cached = store.get(doc_id, page_id)
    if cached is not None and cached[0] == digest:
        return cached[1] or None
    xs = [p[0] for stroke in strokes for p in stroke]
    ys = [p[1] for stroke in strokes for p in stroke]
    png = render_strokes(strokes, max(xs) - min(xs) + 1, max(ys) - min(ys) + 1)
    text = transcriber.transcribe(png).strip()
    store.save(doc_id, page_id, digest, text)
    return text or None


def doc_notes(
    store: MarginNoteStore,
    transcriber,
    sync_dir: Path,
    doc_id: str,
    content: dict,
) -> list[dict]:
    """[{"page_index": int, "text": str}] for every page with pen ink.

    Transcription failures are isolated per page so one bad page never loses
    the notes on the others.
    """
    results = []
    for index, page_id in enumerate(page_ids(content)):
        try:
            text = page_note(store, transcriber, sync_dir, doc_id, page_id)
        except Exception as exc:
            log.warning("margin note transcription failed for page %s: %s", page_id, exc)
            continue
        if text:
            results.append({"page_index": index, "text": text})
    return results
