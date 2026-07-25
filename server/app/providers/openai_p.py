"""OpenAI-compatible API provider (OpenAI, or any gateway via OPENAI_BASE_URL).

Stateless: no CLI sessions. With OPENAI_BASE_URL this covers LiteLLM proxy,
OpenRouter, and Ollama, which is the route to Gemini and other models by API.
"""

import base64
from collections.abc import Iterator

from openai import OpenAI

from ..config import settings
from ..prompting import TRANSCRIBE_SYSTEM
from .base import Event


class OpenAIProvider:
    name = "openai"
    supports_sessions = False

    def __init__(self) -> None:
        kwargs = {"base_url": settings.openai_base_url} if settings.openai_base_url else {}
        self._client = OpenAI(**kwargs)

    def transcribe(self, ink_png: bytes, strong: bool = False) -> str:
        url = f"data:image/png;base64,{base64.standard_b64encode(ink_png).decode()}"
        response = self._client.chat.completions.create(
            model=settings.openai_model if strong else settings.openai_transcribe_model,
            messages=[
                {"role": "system", "content": TRANSCRIBE_SYSTEM},
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": url}},
                        {"type": "text", "text": "Transcribe the handwriting in this image."},
                    ],
                },
            ],
        )
        text = response.choices[0].message.content or ""
        return text.strip() or "[illegible]"

    def answer_events(
        self,
        prompt: str,
        system: str,
        resume_session_id: str | None,
        images: list[str] | None = None,
    ) -> Iterator[Event]:
        user_content: list[dict] | str = prompt
        if images:
            from pathlib import Path

            user_content = [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:image/png;base64,"
                        + base64.standard_b64encode(Path(p).read_bytes()).decode()
                    },
                }
                for p in images
            ] + [{"type": "text", "text": prompt + "\n\n(The current document page is attached as an image.)"}]
        stream = self._client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
            stream=True,
        )
        for chunk in stream:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                yield ("text", delta)
