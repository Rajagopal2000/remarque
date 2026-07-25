"""Gemini provider via the gemini CLI. EXPERIMENTAL and stateless.

The gemini CLI (as of writing) has no verified headless session-resume flow, so
this provider is stateless: the caller embeds document context and history into
every prompt. Images are attached with the @file prompt syntax.
Verified only for basic invocation shape; revisit once the CLI is installed.
"""

import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path

from ..config import settings
from ..prompting import TRANSCRIBE_SYSTEM
from .base import Event


def _require_cli() -> None:
    if shutil.which("gemini") is None:
        raise RuntimeError("gemini CLI not installed; install it and log in first")


def _run(prompt: str, model: str, cwd: str) -> str:
    args = ["gemini", "-p", prompt]
    if model:
        args += ["-m", model]
    proc = subprocess.run(args, capture_output=True, text=True, cwd=cwd, timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(f"gemini exited {proc.returncode}: {proc.stderr.strip()[:500]}")
    return proc.stdout.strip()


class GeminiProvider:
    name = "gemini"
    supports_sessions = False

    def transcribe(self, ink_png: bytes) -> str:
        _require_cli()
        with tempfile.TemporaryDirectory(prefix="remarque-ink-") as tmpdir:
            image_path = Path(tmpdir) / "ink.png"
            image_path.write_bytes(ink_png)
            prompt = f"{TRANSCRIBE_SYSTEM}\nTranscribe the handwriting in @{image_path.name}"
            out = _run(prompt, settings.gemini_transcribe_model, tmpdir)
            return out or "[illegible]"

    def answer_events(
        self,
        prompt: str,
        system: str,
        resume_session_id: str | None,
        images: list[str] | None = None,
    ) -> Iterator[Event]:
        _require_cli()
        full = f"{system}\n\n{prompt}"
        for path in images or []:
            full += f"\n\nThe current document page image: @{path}"
        out = _run(full, settings.gemini_model, str(settings.agent_home))
        yield ("text", out)
