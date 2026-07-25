"""Full-text search (sqlite FTS5) over conversation history and document text.

Both indexes are synced lazily at query time: history rows are added
incrementally by rowid, documents are (re)indexed per page when their source
file changes and pruned when they disappear from the sync dir. No write-path
coupling, and the first search after a change pays the indexing cost.
"""

import logging
import re
import sqlite3
import threading
from pathlib import Path

import pymupdf

from .documents import Document

log = logging.getLogger(__name__)

_lock = threading.Lock()

SNIPPET_TOKENS = 14


def fts_query(text: str) -> str | None:
    """Sanitize free text (often transcribed handwriting) into an FTS5 query.

    Each word becomes a quoted token (implicit AND), so punctuation like the
    trailing "?" of a question can never break the query syntax.
    """
    words = re.findall(r"\w+", text)
    if not words:
        return None
    return " ".join(f'"{w}"' for w in words)


class SearchIndex:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        with self._connect() as conn:
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS turns_fts USING fts5("
                "content, doc_id UNINDEXED, role UNINDEXED, ts UNINDEXED)"
            )
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS doc_fts USING fts5("
                "text, doc_id UNINDEXED, page UNINDEXED)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS doc_index_state ("
                "doc_id TEXT PRIMARY KEY, mtime REAL NOT NULL, size INTEGER NOT NULL)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS search_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

    def _sync_turns(self, conn: sqlite3.Connection) -> None:
        row = conn.execute("SELECT value FROM search_meta WHERE key = 'turns_rowid'").fetchone()
        last = int(row[0]) if row else 0
        rows = conn.execute(
            "SELECT rowid, content, doc_id, role, ts FROM turns WHERE rowid > ?", (last,)
        ).fetchall()
        if not rows:
            return
        conn.executemany(
            "INSERT INTO turns_fts (content, doc_id, role, ts) VALUES (?, ?, ?, ?)",
            [r[1:] for r in rows],
        )
        conn.execute(
            "INSERT OR REPLACE INTO search_meta VALUES ('turns_rowid', ?)",
            (str(max(r[0] for r in rows)),),
        )

    def _sync_documents(self, conn: sqlite3.Connection, docs: list[Document]) -> None:
        current_ids = {d.doc_id for d in docs}
        for (doc_id,) in conn.execute("SELECT doc_id FROM doc_index_state").fetchall():
            if doc_id not in current_ids:
                conn.execute("DELETE FROM doc_fts WHERE doc_id = ?", (doc_id,))
                conn.execute("DELETE FROM doc_index_state WHERE doc_id = ?", (doc_id,))
        for doc in docs:
            if doc.pdf_path is None:
                continue
            stat = doc.pdf_path.stat()
            row = conn.execute(
                "SELECT mtime, size FROM doc_index_state WHERE doc_id = ?", (doc.doc_id,)
            ).fetchone()
            if row is not None and row[0] == stat.st_mtime and row[1] == stat.st_size:
                continue
            conn.execute("DELETE FROM doc_fts WHERE doc_id = ?", (doc.doc_id,))
            try:
                with pymupdf.open(doc.pdf_path) as pdf:
                    conn.executemany(
                        "INSERT INTO doc_fts (text, doc_id, page) VALUES (?, ?, ?)",
                        [
                            (text, doc.doc_id, i + 1)
                            for i, page in enumerate(pdf)
                            if (text := page.get_text().strip())
                        ],
                    )
            except Exception as exc:
                log.warning("failed to index %s: %s", doc.pdf_path.name, exc)
                continue
            conn.execute(
                "INSERT OR REPLACE INTO doc_index_state VALUES (?, ?, ?)",
                (doc.doc_id, stat.st_mtime, stat.st_size),
            )

    def search(self, query: str, docs: list[Document], limit: int = 10) -> dict:
        """{"history": [...], "documents": [...]} hits ranked by bm25."""
        match = fts_query(query)
        if match is None:
            return {"history": [], "documents": []}
        titles = {d.doc_id: d.title for d in docs}
        with _lock, self._connect() as conn:
            self._sync_turns(conn)
            self._sync_documents(conn, docs)
            history = [
                {
                    "doc_id": doc_id,
                    "title": titles.get(doc_id, "General questions"),
                    "role": role,
                    "ts": ts,
                    "snippet": snippet,
                }
                for snippet, doc_id, role, ts in conn.execute(
                    f"SELECT snippet(turns_fts, 0, '*', '*', '…', {SNIPPET_TOKENS}),"
                    " doc_id, role, ts FROM turns_fts WHERE turns_fts MATCH ?"
                    " ORDER BY bm25(turns_fts) LIMIT ?",
                    (match, limit),
                )
            ]
            documents = [
                {
                    "doc_id": doc_id,
                    "title": titles.get(doc_id, doc_id),
                    "page": page,
                    "snippet": snippet,
                }
                for snippet, doc_id, page in conn.execute(
                    f"SELECT snippet(doc_fts, 0, '*', '*', '…', {SNIPPET_TOKENS}),"
                    " doc_id, page FROM doc_fts WHERE doc_fts MATCH ?"
                    " ORDER BY bm25(doc_fts) LIMIT ?",
                    (match, limit),
                )
            ]
        return {"history": history, "documents": documents}
