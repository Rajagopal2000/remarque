"""Per-document conversation history in sqlite."""

import sqlite3
import threading
import time
from pathlib import Path

_lock = threading.Lock()


class History:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS turns ("
                "doc_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, ts REAL NOT NULL)"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_turns_doc ON turns(doc_id, ts)")
            cols = [row[1] for row in conn.execute("PRAGMA table_info(turns)")]
            if "page" not in cols:
                conn.execute("ALTER TABLE turns ADD COLUMN page INTEGER")

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

    def add(self, doc_id: str, role: str, content: str, page: int | None = None) -> None:
        with _lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO turns (doc_id, role, content, ts, page) VALUES (?, ?, ?, ?, ?)",
                (doc_id, role, content, time.time(), page),
            )

    def recent(self, doc_id: str, limit: int = 12) -> list[dict]:
        with _lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT role, content, ts, page FROM turns WHERE doc_id = ? "
                "ORDER BY ts DESC LIMIT ?",
                (doc_id, limit),
            ).fetchall()
        return [{"role": r, "content": c, "ts": t, "page": p} for r, c, t, p in reversed(rows)]

    def since(self, ts: float) -> list[dict]:
        """All turns across documents from ts onward (for the reading digest)."""
        with _lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT doc_id, role, content, ts, page FROM turns WHERE ts >= ? ORDER BY ts",
                (ts,),
            ).fetchall()
        return [
            {"doc_id": d, "role": r, "content": c, "ts": t, "page": p} for d, r, c, t, p in rows
        ]
