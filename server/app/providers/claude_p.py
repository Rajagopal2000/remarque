"""Anthropic API provider (needs ANTHROPIC_API_KEY). Stateless: no CLI sessions."""

import base64
from collections.abc import Iterator

import anthropic

from ..config import settings
from ..prompting import TRANSCRIBE_SYSTEM
from .base import Event


class ClaudeProvider:
    name = "claude"
    supports_sessions = False

    def __init__(self) -> None:
        self._client = anthropic.Anthropic()

    def transcribe(self, ink_png: bytes) -> str:
        response = self._client.messages.create(
            model=settings.anthropic_transcribe_model,
            max_tokens=500,
            system=TRANSCRIBE_SYSTEM,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": base64.standard_b64encode(ink_png).decode(),
                            },
                        },
                        {"type": "text", "text": "Transcribe the handwriting in this image."},
                    ],
                }
            ],
        )
        text = next((b.text for b in response.content if b.type == "text"), "")
        return text.strip() or "[illegible]"

    def answer_events(
        self,
        prompt: str,
        system: str,
        resume_session_id: str | None,
        images: list[str] | None = None,
    ) -> Iterator[Event]:
        content: list[dict] | str = prompt
        if images:
            from pathlib import Path

            content = [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": base64.standard_b64encode(Path(p).read_bytes()).decode(),
                    },
                }
                for p in images
            ] + [{"type": "text", "text": prompt + "\n\n(The current document page is attached as an image.)"}]
        with self._client.messages.stream(
            model=settings.anthropic_model,
            max_tokens=settings.max_answer_tokens,
            thinking={"type": "adaptive"},
            system=system,
            messages=[{"role": "user", "content": content}],
        ) as stream:
            for chunk in stream.text_stream:
                yield ("text", chunk)
            final = stream.get_final_message()
            yield (
                "usage",
                {
                    "input_tokens": final.usage.input_tokens,
                    "cached_input_tokens": final.usage.cache_read_input_tokens,
                    "output_tokens": final.usage.output_tokens,
                    "cost_usd": None,
                },
            )
