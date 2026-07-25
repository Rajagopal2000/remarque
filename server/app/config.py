"""Server configuration loaded from environment / .env file.

This file is the single registry of every environment variable the service
uses. `.env.example` mirrors it for humans, and tests/test_env_catalog.py
fails the build if the two drift apart. The Kubernetes deployment template
sets only the variables that differ from these defaults.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

DATA_DIR = Path(os.environ.get("REMARQUE_DATA_DIR", Path.home() / ".remarque"))

# Consumed implicitly by third-party code, never read in this file:
# - ANTHROPIC_API_KEY: anthropic SDK (PROVIDER=claude); the claude-code
#   provider actively removes it so it cannot override subscription auth
# - OPENAI_API_KEY: openai SDK (PROVIDER=openai, or gateways via OPENAI_BASE_URL)
# - CLAUDE_CODE_OAUTH_TOKEN: claude CLI subscription auth in containers
IMPLICIT_ENV_VARS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")


@dataclass
class Settings:
    rm_host: str = os.environ.get("RM_HOST", "10.11.99.1")
    rm_user: str = os.environ.get("RM_USER", "root")
    ssh_key_path: str = os.environ.get("SSH_KEY_PATH", "")
    xochitl_remote_dir: str = os.environ.get(
        "XOCHITL_REMOTE_DIR", "/home/root/.local/share/remarkable/xochitl"
    )
    sync_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("SYNC_DIR", DATA_DIR / "xochitl"))
    )
    history_db: Path = field(
        default_factory=lambda: Path(os.environ.get("HISTORY_DB", DATA_DIR / "history.db"))
    )
    # Answer provider: claude-code | codex | gemini | claude (API key) | openai
    provider: str = os.environ.get("PROVIDER", "claude-code")
    # Transcription provider; empty means same as the answer provider.
    transcribe_provider: str = os.environ.get("TRANSCRIBE_PROVIDER", "")

    # Per-provider model pairs: cheap model for transcription, strong for answers.
    claude_code_model: str = os.environ.get("CLAUDE_CODE_MODEL", "sonnet")
    claude_code_transcribe_model: str = os.environ.get("CLAUDE_CODE_TRANSCRIBE_MODEL", "sonnet")
    codex_model: str = os.environ.get("CODEX_MODEL", "")  # empty = CLI default
    codex_transcribe_model: str = os.environ.get("CODEX_TRANSCRIBE_MODEL", "")
    gemini_model: str = os.environ.get("GEMINI_MODEL", "")
    gemini_transcribe_model: str = os.environ.get("GEMINI_TRANSCRIBE_MODEL", "gemini-2.5-flash")
    anthropic_model: str = os.environ.get("ANTHROPIC_MODEL", "claude-opus-4-8")
    anthropic_transcribe_model: str = os.environ.get("ANTHROPIC_TRANSCRIBE_MODEL", "claude-haiku-4-5")
    openai_model: str = os.environ.get("OPENAI_MODEL", "gpt-4o")
    openai_transcribe_model: str = os.environ.get("OPENAI_TRANSCRIBE_MODEL", "gpt-4o-mini")
    openai_base_url: str = os.environ.get("OPENAI_BASE_URL", "")

    # Shared secret required in the X-Api-Token header on /api routes.
    # Empty disables auth (local development only).
    api_token: str = os.environ.get("API_TOKEN", "")

    session_ttl_days: int = int(os.environ.get("SESSION_TTL_DAYS", "730"))
    # Skip document syncs fresher than this many seconds (keeps rsync out of the ask path).
    sync_max_age: int = int(os.environ.get("SYNC_MAX_AGE", "60"))
    # Optional AnkiConnect endpoint (Anki desktop + AnkiConnect plugin); when set,
    # generated decks are pushed there and a sync to AnkiWeb is triggered.
    anki_connect_url: str = os.environ.get("ANKI_CONNECT_URL", "")
    # Optional folder inside an Obsidian vault; when set, notes export also
    # writes a markdown note per document there.
    obsidian_dir: str = os.environ.get("OBSIDIAN_DIR", "")
    # Refresh existing Anki decks incrementally every N hours (0 disables).
    # Only documents whose deck was created once (Anki button) are updated,
    # and unchanged documents skip the LLM entirely.
    anki_auto_hours: float = float(os.environ.get("ANKI_AUTO_HOURS", "0"))
    # Write a reading digest note into OBSIDIAN_DIR every N days (0 disables;
    # needs OBSIDIAN_DIR). Deterministic: no LLM call involved.
    digest_every_days: float = float(os.environ.get("DIGEST_EVERY_DAYS", "0"))
    agent_home: Path = field(
        default_factory=lambda: Path(os.environ.get("AGENT_HOME", DATA_DIR / "agent-home"))
    )
    max_answer_tokens: int = int(os.environ.get("MAX_ANSWER_TOKENS", "8000"))
    max_doc_chars: int = int(os.environ.get("MAX_DOC_CHARS", "150000"))


settings = Settings()
settings.sync_dir.mkdir(parents=True, exist_ok=True)
settings.history_db.parent.mkdir(parents=True, exist_ok=True)
settings.agent_home.mkdir(parents=True, exist_ok=True)
