"""Drift guard: app/config.py is the single registry of environment variables,
and .env.example must mirror it exactly (plus the documented implicit vars)."""

import re
from pathlib import Path

from app.config import IMPLICIT_ENV_VARS

SERVER_DIR = Path(__file__).resolve().parent.parent


def config_vars() -> set[str]:
    source = (SERVER_DIR / "app" / "config.py").read_text()
    return set(re.findall(r'os\.environ\.get\(\s*"([A-Z0-9_]+)"', source))


def env_example_vars() -> set[str]:
    names = set()
    for line in (SERVER_DIR / ".env.example").read_text().splitlines():
        match = re.match(r"^#?\s*([A-Z][A-Z0-9_]*)=", line)
        if match:
            names.add(match.group(1))
    return names


def test_every_config_var_is_documented():
    missing = config_vars() - env_example_vars()
    assert not missing, f".env.example is missing: {sorted(missing)}"


def test_every_documented_var_is_real():
    known = config_vars() | set(IMPLICIT_ENV_VARS)
    unknown = env_example_vars() - known
    assert not unknown, (
        f".env.example documents variables nothing consumes: {sorted(unknown)} "
        "(add them to config.py or IMPLICIT_ENV_VARS, or remove them)"
    )
