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

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

    def add(self, doc_id: str, role: str, content: str) -> None:
        with _lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO turns (doc_id, role, content, ts) VALUES (?, ?, ?, ?)",
                (doc_id, role, content, time.time()),
            )

    def recent(self, doc_id: str, limit: int = 12) -> list[dict]:
        with _lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT role, content, ts FROM turns WHERE doc_id = ? ORDER BY ts DESC LIMIT ?",
                (doc_id, limit),
            ).fetchall()
        return [{"role": r, "content": c, "ts": t} for r, c, t in reversed(rows)]
