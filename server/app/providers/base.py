"""Provider abstraction for the two-stage flow.

Each provider offers:
- transcribe(ink_png) -> question text, using its cheap model (stateless, one-shot)
- answer_events(prompt, system, resume_session_id, images) -> iterator of events:
    ("text", str)     incremental answer text
    ("session", str)  the provider session id to store for resuming (session providers)
    ("usage", dict)   token usage for this ask
images is an optional list of PNG file paths (e.g. the current document page)
that the provider attaches in whatever way its backend supports.
Providers with supports_sessions=False ignore resume ids; the caller then embeds
document context and conversation history into every prompt instead.
"""

from collections.abc import Iterator
from typing import Protocol

Event = tuple[str, object]


class Provider(Protocol):
    name: str
    supports_sessions: bool

    def transcribe(self, ink_png: bytes) -> str: ...

    def answer_events(
        self,
        prompt: str,
        system: str,
        resume_session_id: str | None,
        images: list[str] | None = None,
    ) -> Iterator[Event]: ...


_instances: dict[str, Provider] = {}


def get_provider(name: str) -> Provider:
    if name in _instances:
        return _instances[name]
    if name == "claude-code":
        from .claude_code_p import ClaudeCodeProvider

        provider: Provider = ClaudeCodeProvider()
    elif name == "codex":
        from .codex_p import CodexProvider

        provider = CodexProvider()
    elif name == "gemini":
        from .gemini_p import GeminiProvider

        provider = GeminiProvider()
    elif name == "claude":
        from .claude_p import ClaudeProvider

        provider = ClaudeProvider()
    elif name == "openai":
        from .openai_p import OpenAIProvider

        provider = OpenAIProvider()
    else:
        raise ValueError(f"unknown provider: {name}")
    _instances[name] = provider
    return provider
