"""In-memory job store: each ask runs a worker thread; the tablet polls snapshots."""

import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class Job:
    job_id: str
    status: str = "running"  # running | done | error | cancelled
    phase: str = "transcribing"  # transcribing | answering
    text: str = ""
    question_read: str = ""
    error: str = ""
    usage: dict | None = None
    session: dict | None = None
    cancelled: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def append(self, chunk: str) -> None:
        with self._lock:
            self.text += chunk

    def snapshot(self, cursor: int) -> dict:
        with self._lock:
            status = "cancelled" if self.cancelled else self.status
            return {
                "status": status,
                "phase": self.phase,
                "question_read": self.question_read,
                "text_so_far": self.text[cursor:],
                "next_cursor": len(self.text),
                "usage": self.usage,
                "session": self.session,
                "error": self.error,
            }


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def start(self, work: Callable[[Job], None]) -> str:
        """Run work(job) in a thread; it fills the job and may raise to fail it."""
        job = Job(job_id=uuid.uuid4().hex[:12])
        with self._lock:
            self._jobs[job.job_id] = job

        def runner() -> None:
            try:
                work(job)
                job.status = "cancelled" if job.cancelled else "done"
            except Exception as exc:
                job.error = str(exc)
                job.status = "error"

        threading.Thread(target=runner, daemon=True).start()
        return job.job_id

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> bool:
        job = self.get(job_id)
        if job is None:
            return False
        job.cancelled = True
        return True


store = JobStore()
