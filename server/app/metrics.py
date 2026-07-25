"""Prometheus metrics for the companion service."""

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

ASKS = Counter("remarque_asks_total", "Questions/jobs started", ["kind"])
ERRORS = Counter("remarque_errors_total", "Failed jobs", ["kind"])
CANCELLED = Counter("remarque_cancelled_total", "Cancelled jobs", ["kind"])
TRANSCRIBE_SECONDS = Histogram(
    "remarque_transcribe_seconds", "Handwriting transcription latency",
    buckets=(1, 2, 5, 10, 20, 40, 90),
)
ANSWER_SECONDS = Histogram(
    "remarque_answer_seconds", "Answer generation latency",
    buckets=(2, 5, 10, 20, 40, 90, 180, 400),
)
TOKENS = Counter("remarque_tokens_total", "LLM tokens used", ["provider", "direction"])
SYNC_FAILURES = Counter("remarque_sync_failures_total", "Document sync failures")

__all__ = [
    "ASKS",
    "ERRORS",
    "CANCELLED",
    "TRANSCRIBE_SECONDS",
    "ANSWER_SECONDS",
    "TOKENS",
    "SYNC_FAILURES",
    "generate_latest",
    "CONTENT_TYPE_LATEST",
]
