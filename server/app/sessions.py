"""Per-document agent session mapping: doc_id + provider -> resumable session id."""

import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

_lock = threading.Lock()


@dataclass
class SessionInfo:
    session_id: str
    created_at: float
    last_used: float
    turns: int

    def to_dict(self, ttl_days: int) -> dict:
        now = time.time()
        return {
            "turns": self.turns,
            "age_days": round((now - self.created_at) / 86400, 1),
            "idle_days": round((now - self.last_used) / 86400, 1),
            "expires_in_days": round(ttl_days - (now - self.last_used) / 86400, 1),
        }


class SessionStore:
    def __init__(self, db_path: Path, ttl_days: int) -> None:
        self._db_path = db_path
        self._ttl_seconds = ttl_days * 86400
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS doc_sessions ("
                "doc_id TEXT NOT NULL, provider TEXT NOT NULL, session_id TEXT NOT NULL,"
                "created_at REAL NOT NULL, last_used REAL NOT NULL, turns INTEGER NOT NULL,"
                "PRIMARY KEY (doc_id, provider))"
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

    def get(self, doc_id: str, provider: str) -> SessionInfo | None:
        """Return the live session, dropping it if past the TTL since last use."""
        with _lock, self._connect() as conn:
            row = conn.execute(
                "SELECT session_id, created_at, last_used, turns FROM doc_sessions "
                "WHERE doc_id = ? AND provider = ?",
                (doc_id, provider),
            ).fetchone()
            if row is None:
                return None
            info = SessionInfo(*row)
            if time.time() - info.last_used > self._ttl_seconds:
                conn.execute(
                    "DELETE FROM doc_sessions WHERE doc_id = ? AND provider = ?",
                    (doc_id, provider),
                )
                return None
            return info

    def record_use(self, doc_id: str, provider: str, session_id: str) -> None:
        """Upsert after an ask: new mapping, or bump turns/last_used (session id may rotate)."""
        now = time.time()
        with _lock, self._connect() as conn:
            row = conn.execute(
                "SELECT turns FROM doc_sessions WHERE doc_id = ? AND provider = ?",
                (doc_id, provider),
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO doc_sessions VALUES (?, ?, ?, ?, ?, 1)",
                    (doc_id, provider, session_id, now, now),
                )
            else:
                conn.execute(
                    "UPDATE doc_sessions SET session_id = ?, last_used = ?, turns = turns + 1 "
                    "WHERE doc_id = ? AND provider = ?",
                    (session_id, now, doc_id, provider),
                )

    def clear(self, doc_id: str) -> int:
        with _lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM doc_sessions WHERE doc_id = ?", (doc_id,))
            return cur.rowcount
