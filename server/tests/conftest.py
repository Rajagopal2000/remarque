"""Isolate the suite from the developer's local server/.env: config.py loads it
at import time, so settings like API_TOKEN would otherwise leak into tests."""

import pytest

from app.config import settings


@pytest.fixture(autouse=True)
def _neutral_settings(monkeypatch):
    monkeypatch.setattr(settings, "api_token", "")
    monkeypatch.setattr(settings, "anki_connect_url", "")
    monkeypatch.setattr(settings, "obsidian_dir", "")
