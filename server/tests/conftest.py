"""Shared pytest fixtures for recipe-agent-mcp server tests."""
import sys
import os

import dotenv
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

FAKE_ENV = {
    "AGORA_APP_ID": "test_app_id",
    "AGORA_APP_CERTIFICATE": "test_app_certificate",
    "MCP_ENDPOINT": "https://fake.example.com/mcp",
    "MURF_API_KEY": "test_murf_key",
}


@pytest.fixture
def fake_env(monkeypatch):
    """Neutralize dotenv and inject required env vars."""
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: False)
    for key, value in FAKE_ENV.items():
        monkeypatch.setenv(key, value)
    yield FAKE_ENV


@pytest.fixture
def shop(tmp_path, monkeypatch):
    """A freshly seeded shop database in a temp file."""
    import shop_db

    monkeypatch.setattr(shop_db, "DB_PATH", str(tmp_path / "shop.db"))
    shop_db.init_db(reset=True)
    import dispatch

    dispatch.reset()
    return shop_db
