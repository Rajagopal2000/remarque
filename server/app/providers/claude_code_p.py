"""Claude subscription provider: headless Claude Code via the Agent SDK.

Transcription: one-shot with the cheap model (default haiku), image read via Read tool.
Answers: text-only turns in a resumable session with the strong model (default sonnet).
Sessions are tied to a stable working directory (settings.agent_home).
"""

import asyncio
import json
import os
import tempfile
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

from ..config import settings
from .base import Event


def _run_async_events(agen: AsyncIterator[Event]) -> Iterator[Event]:
    """Drive an async event generator from sync code."""
    loop = asyncio.new_event_loop()
    try:
        while True:
            try:
                yield loop.run_until_complete(agen.__anext__())
            except StopAsyncIteration:
                break
    finally:
        loop.run_until_complete(agen.aclose())
        loop.close()


class ClaudeCodeProvider:
    name = "claude-code"
    supports_sessions = True

    def __init__(self) -> None:
        # A set ANTHROPIC_API_KEY overrides subscription auth in the spawned
        # claude process. This provider exists to use the subscription, so drop it.
        os.environ.pop("ANTHROPIC_API_KEY", None)
        # Claude Code prunes session transcripts after cleanupPeriodDays (default
        # ~30 days), which would break resuming long-lived per-document sessions.
        # Pass a scoped settings file so retention outlives the session TTL.
        self._settings_path = settings.agent_home / "claude-settings.json"
        retention = {"cleanupPeriodDays": settings.session_ttl_days + 30}
        self._settings_path.write_text(json.dumps(retention))

    # -- transcription (cheap model, one shot) --

    def transcribe(self, ink_png: bytes) -> str:
        with tempfile.TemporaryDirectory(prefix="remarque-ink-") as tmpdir:
            image_path = Path(tmpdir) / "ink.png"
            image_path.write_bytes(ink_png)
            prompt = (
                f"Read the image file at {image_path} and transcribe the handwriting in it."
            )

            async def run() -> str:
                from claude_agent_sdk import ClaudeAgentOptions, query

                from ..prompting import TRANSCRIBE_SYSTEM

                options = ClaudeAgentOptions(
                    system_prompt=TRANSCRIBE_SYSTEM,
                    allowed_tools=["Read"],
                    max_turns=3,
                    model=settings.claude_code_transcribe_model,
                    cwd=tmpdir,
                )
                parts: list[str] = []
                async for message in query(prompt=prompt, options=options):
                    kind = type(message).__name__
                    if kind == "AssistantMessage":
                        for block in getattr(message, "content", []):
                            text = getattr(block, "text", None)
                            if text:
                                parts.append(text)
                    elif kind == "ResultMessage" and getattr(message, "is_error", False):
                        raise RuntimeError(getattr(message, "result", "transcription failed"))
                return parts[-1].strip() if parts else "[illegible]"

            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(run())
            finally:
                loop.close()

    # -- answers (strong model, resumable session) --

    async def _answer(
        self,
        prompt: str,
        system: str,
        resume_session_id: str | None,
        images: list[str] | None,
    ) -> AsyncIterator[Event]:
        from claude_agent_sdk import ClaudeAgentOptions, query

        if images:
            for path in images:
                prompt += f"\n\nAn image of the current document page is at: {path}\nUse the Read tool to view it before answering."
        options = ClaudeAgentOptions(
            system_prompt=system,
            allowed_tools=["Read"] if images else [],
            max_turns=2 + (2 * len(images) if images else 0),
            model=settings.claude_code_model,
            cwd=str(settings.agent_home),
            resume=resume_session_id,
            settings=str(self._settings_path),
        )
        async for message in query(prompt=prompt, options=options):
            kind = type(message).__name__
            if kind == "AssistantMessage":
                for block in getattr(message, "content", []):
                    text = getattr(block, "text", None)
                    if text:
                        yield ("text", text)
            elif kind == "ResultMessage":
                if getattr(message, "is_error", False):
                    raise RuntimeError(getattr(message, "result", "claude code error"))
                session_id = getattr(message, "session_id", None)
                if session_id:
                    yield ("session", session_id)
                usage = getattr(message, "usage", None) or {}
                yield (
                    "usage",
                    {
                        "input_tokens": usage.get("input_tokens"),
                        "cached_input_tokens": usage.get("cache_read_input_tokens"),
                        "output_tokens": usage.get("output_tokens"),
                        "cost_usd": getattr(message, "total_cost_usd", None),
                    },
                )

    def answer_events(
        self,
        prompt: str,
        system: str,
        resume_session_id: str | None,
        images: list[str] | None = None,
    ) -> Iterator[Event]:
        yield from _run_async_events(self._answer(prompt, system, resume_session_id, images))
