"""Codex (ChatGPT subscription) provider via the codex CLI.

Transcription: one-shot `codex exec --ephemeral` with the image attached (-i).
Answers: `codex exec` threads resumed with `codex exec resume <id>`.
JSONL events: thread.started (session id), item.completed/agent_message (text),
turn.completed (usage).
"""

import json
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path

from ..config import settings
from ..prompting import TRANSCRIBE_SYSTEM
from .base import Event

BASE_FLAGS = ["--json", "--skip-git-repo-check", "-s", "read-only", "--color", "never"]


def _run(args: list[str]) -> subprocess.Popen:
    return subprocess.Popen(
        ["codex", "exec", *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(settings.agent_home),
    )


def _events(proc: subprocess.Popen) -> Iterator[Event]:
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        etype = event.get("type", "")
        if etype == "thread.started" and event.get("thread_id"):
            yield ("session", event["thread_id"])
        elif etype == "item.completed":
            item = event.get("item", {})
            if item.get("type") == "agent_message" and item.get("text"):
                yield ("text", item["text"])
        elif etype == "turn.completed":
            usage = event.get("usage", {})
            yield (
                "usage",
                {
                    "input_tokens": usage.get("input_tokens"),
                    "cached_input_tokens": usage.get("cached_input_tokens"),
                    "output_tokens": usage.get("output_tokens"),
                    "cost_usd": None,
                },
            )
        elif etype in ("turn.failed", "error"):
            raise RuntimeError(event.get("message") or json.dumps(event))
    proc.wait()
    if proc.returncode != 0:
        stderr = proc.stderr.read() if proc.stderr else ""
        raise RuntimeError(f"codex exited {proc.returncode}: {stderr.strip()[:500]}")


class CodexProvider:
    name = "codex"
    supports_sessions = True

    def transcribe(self, ink_png: bytes, strong: bool = False) -> str:
        with tempfile.TemporaryDirectory(prefix="remarque-ink-") as tmpdir:
            image_path = Path(tmpdir) / "ink.png"
            image_path.write_bytes(ink_png)
            args = [*BASE_FLAGS, "--ephemeral", "-i", str(image_path)]
            model = settings.codex_model if strong else settings.codex_transcribe_model
            if model:
                args += ["-m", model]
            args.append(TRANSCRIBE_SYSTEM + "\nTranscribe the handwriting in the attached image.")
            parts = [payload for kind, payload in _events(_run(args)) if kind == "text"]
            return str(parts[-1]).strip() if parts else "[illegible]"

    def answer_events(
        self,
        prompt: str,
        system: str,
        resume_session_id: str | None,
        images: list[str] | None = None,
    ) -> Iterator[Event]:
        if resume_session_id:
            args = ["resume", resume_session_id, *BASE_FLAGS]
        else:
            args = list(BASE_FLAGS)
            # codex has no separate system prompt; fold it into the session's first message
            prompt = f"{system}\n\n{prompt}"
        if settings.codex_model:
            args += ["-m", settings.codex_model]
        for path in images or []:
            args += ["-i", path]
        if images:
            prompt += "\n\n(An image of the current document page is attached.)"
        args.append(prompt)
        yield from _events(_run(args))
