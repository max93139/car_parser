"""
Unit tests for configuration loading, Pydantic v2 settings, and environment overrides.
"""

from __future__ import annotations

import os
from src.config import Settings, get_settings


def test_default_config_loading():
    settings = Settings.load("config/config.yaml")

    # App section
    assert settings.app.env == "production"
    assert settings.app.log_level == "INFO"
    assert settings.app.timezone == "Europe/Kyiv"

    # Search section
    assert settings.search.brand == "Audi"
    assert settings.search.model == "A6"
    assert settings.search.generation == "C5"
    assert settings.search.min_year == 1997
    assert settings.search.max_year == 2005

    engine_codes = [e.code for e in settings.search.target_engines]
    assert "1.8T" in engine_codes
    assert "2.4" in engine_codes
    assert "1.9TDI" in engine_codes

    # Parsers
    assert settings.parsers.auto_ria.enabled is True
    assert settings.parsers.olx.enabled is True
    assert settings.parsers.rst.enabled is True
    assert settings.parsers.telegram.enabled is True
    assert len(settings.parsers.telegram.channels) >= 3
    assert settings.parsers.instagram.enabled is True
    assert len(settings.parsers.instagram.accounts) >= 3

    # Database
    assert "postgresql+asyncpg" in settings.database.url


def test_env_var_overrides(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///custom_test.db")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "@audi_alerts_channel")
    monkeypatch.setenv("TELEGRAM_API_ID", "987654")
    monkeypatch.setenv("TELEGRAM_API_HASH", "abcdef0123456789abcdef0123456789")
    monkeypatch.setenv("TELEGRAM_SESSION_STRING", "1BVtsOKEBuxXXXX...")
    monkeypatch.setenv("INSTAGRAM_SESSION_COOKIE", "sessionid=xyz987")

    settings = Settings.load("config/config.yaml")

    assert settings.database.url == "sqlite+aiosqlite:///custom_test.db"
    assert settings.DATABASE_URL == "sqlite+aiosqlite:///custom_test.db"
    assert settings.telegram_bot.bot_token == "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
    assert settings.telegram_bot.chat_id == "@audi_alerts_channel"
    assert settings.parsers.telegram.api_id == 987654
    assert settings.parsers.telegram.api_hash == "abcdef0123456789abcdef0123456789"
    assert settings.parsers.telegram.session_string == "1BVtsOKEBuxXXXX..."
    assert settings.parsers.instagram.session_cookie == "sessionid=xyz987"


def test_get_settings_singleton():
    s1 = get_settings()
    s2 = get_settings()
    assert s1 is s2

    s3 = get_settings(reload=True)
    assert s3 is not None
