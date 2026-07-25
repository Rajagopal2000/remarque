import time

from app.sessions import SessionStore


def test_session_lifecycle(tmp_path):
    store = SessionStore(tmp_path / "s.db", ttl_days=60)
    assert store.get("doc1", "claude-code") is None

    store.record_use("doc1", "claude-code", "sess-abc")
    info = store.get("doc1", "claude-code")
    assert info.session_id == "sess-abc"
    assert info.turns == 1

    store.record_use("doc1", "claude-code", "sess-abc")
    assert store.get("doc1", "claude-code").turns == 2

    assert store.clear("doc1") == 1
    assert store.get("doc1", "claude-code") is None


def test_session_ttl_expiry(tmp_path, monkeypatch):
    store = SessionStore(tmp_path / "s.db", ttl_days=60)
    store.record_use("doc1", "claude-code", "sess-abc")
    future = time.time() + 61 * 86400
    monkeypatch.setattr(time, "time", lambda: future)
    assert store.get("doc1", "claude-code") is None


def test_codex_event_parsing():
    """Parse the exact JSONL shapes recorded from codex exec 0.144."""
    import io
    import json
    from unittest.mock import MagicMock

    from app.providers.codex_p import _events

    lines = [
        {"type": "thread.started", "thread_id": "th-123"},
        {"type": "turn.started"},
        {"type": "item.completed", "item": {"id": "item_0", "type": "agent_message", "text": "OK"}},
        {
            "type": "turn.completed",
            "usage": {"input_tokens": 15503, "cached_input_tokens": 0, "output_tokens": 5},
        },
    ]
    proc = MagicMock()
    proc.stdout = io.StringIO("\n".join(json.dumps(x) for x in lines))
    proc.returncode = 0
    events = list(_events(proc))
    assert ("session", "th-123") in events
    assert ("text", "OK") in events
    usage = dict(events)["usage"]
    assert usage["input_tokens"] == 15503
