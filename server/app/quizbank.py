"""Structured quiz results: what was asked and how the answer was graded.

The graded answer's first words carry the verdict (the grading prompt demands
"Correct / Partially correct / Incorrect" up front); weak spots are questions
whose latest attempt was not fully correct, and they steer the next quiz.
"""

import re
import sqlite3
import threading
import time
from pathlib import Path

_lock = threading.Lock()


def parse_verdict(grade_text: str) -> str:
    head = grade_text.strip().lower()
    # Models decorate the verdict despite the prompt ("**Correct**",
    # "Verdict: correct"); strip the lead-in before matching.
    head = re.sub(r"^[^a-z]+", "", head)
    head = re.sub(r"^verdict[^a-z]+", "", head)
    if head.startswith("partially"):
        return "partial"
    if head.startswith("correct"):
        return "correct"
    if head.startswith("incorrect"):
        return "incorrect"
    return "unknown"


class QuizResultStore:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS quiz_results ("
                "doc_id TEXT NOT NULL, question TEXT NOT NULL, verdict TEXT NOT NULL,"
                "ts REAL NOT NULL)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_quiz_doc ON quiz_results(doc_id, ts)"
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

    def add(self, doc_id: str, question: str, verdict: str) -> None:
        with _lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO quiz_results (doc_id, question, verdict, ts) VALUES (?, ?, ?, ?)",
                (doc_id, question, verdict, time.time()),
            )

    def weak_spots(self, doc_id: str, limit: int = 8) -> list[str]:
        """Questions whose LATEST attempt was not fully correct, newest first.

        A question later answered correctly is considered mastered and dropped.
        """
        with _lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT question, verdict FROM quiz_results WHERE doc_id = ? ORDER BY ts",
                (doc_id,),
            ).fetchall()
        latest: dict[str, str] = {}
        order: list[str] = []
        for question, verdict in rows:
            if question not in latest:
                order.append(question)
            latest[question] = verdict
        weak = [q for q in order if latest[q] != "correct"]
        return weak[-limit:][::-1]

    def since(self, ts: float) -> list[dict]:
        """All results across documents from ts onward (for the reading digest)."""
        with _lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT doc_id, question, verdict, ts FROM quiz_results "
                "WHERE ts >= ? ORDER BY ts",
                (ts,),
            ).fetchall()
        return [{"doc_id": d, "question": q, "verdict": v, "ts": t} for d, q, v, t in rows]
