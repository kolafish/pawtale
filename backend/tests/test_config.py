import pytest
from fastapi import Response

from app import auth
from app import database
from app.auth import SESSION_COOKIE_NAME, set_session_cookie
from app.config import (
    DEFAULT_APP_SECRET_KEY,
    Settings,
    is_production_environment,
    validate_runtime_settings,
)


def test_validate_runtime_settings_rejects_invalid_app_env():
    settings = Settings(app_env="prod", app_secret_key="x" * 32)

    with pytest.raises(RuntimeError, match="APP_ENV must be one of"):
        validate_runtime_settings(settings)


def test_validate_runtime_settings_rejects_default_production_secret():
    settings = Settings(app_env="production", app_secret_key=DEFAULT_APP_SECRET_KEY)

    with pytest.raises(RuntimeError, match="non-default"):
        validate_runtime_settings(settings)


def test_validate_runtime_settings_rejects_short_production_secret():
    settings = Settings(app_env="production", app_secret_key="short-secret")

    with pytest.raises(RuntimeError, match="at least 32 characters"):
        validate_runtime_settings(settings)


def test_validate_runtime_settings_accepts_strong_production_secret():
    settings = Settings(app_env=" Production ", app_secret_key="x" * 32)

    validate_runtime_settings(settings)
    assert is_production_environment(settings)


def test_session_cookie_is_secure_in_production(monkeypatch):
    settings = Settings(app_env="production", app_secret_key="x" * 32)
    monkeypatch.setattr(auth, "get_settings", lambda: settings)

    response = Response()
    set_session_cookie(response, "user-id")

    set_cookie = response.headers["set-cookie"]
    assert SESSION_COOKIE_NAME in set_cookie
    assert "Secure" in set_cookie


def test_session_cookie_remains_http_compatible_in_development(monkeypatch):
    settings = Settings(app_env="development", app_secret_key=DEFAULT_APP_SECRET_KEY)
    monkeypatch.setattr(auth, "get_settings", lambda: settings)

    response = Response()
    set_session_cookie(response, "user-id")

    assert "Secure" not in response.headers["set-cookie"]


def test_sqlite_engine_connect_args_use_busy_timeout_seconds():
    settings = Settings(
        database_url="sqlite+aiosqlite:///tmp/pawtale.db",
        sqlite_busy_timeout_ms=7500,
    )

    assert database.sqlite_engine_connect_args(settings) == {"timeout": 7.5}


def test_non_sqlite_engine_connect_args_stay_empty():
    settings = Settings(database_url="postgresql+asyncpg://pawtale@example.test/db")

    assert database.sqlite_engine_connect_args(settings) == {}


def test_small_concurrency_provider_limit_defaults():
    settings = Settings()

    assert settings.voice_gateway_max_active_sessions == 10
    assert settings.voice_gateway_max_active_sessions_per_user == 1
    assert settings.ai_text_max_concurrent_requests == 3
    assert settings.tts_max_concurrent_requests == 3
    assert settings.tts_alignment_max_concurrent_requests == 2


def test_configure_sqlite_connection_applies_small_concurrency_pragmas():
    settings = Settings(
        sqlite_busy_timeout_ms=3000,
        sqlite_journal_mode="wal",
        sqlite_synchronous="normal",
    )
    connection = FakeDBAPIConnection()

    database.configure_sqlite_connection(connection, settings)

    assert connection.cursor_obj.statements == [
        "PRAGMA busy_timeout = 3000",
        "PRAGMA journal_mode = WAL",
        "PRAGMA synchronous = NORMAL",
    ]
    assert connection.cursor_obj.closed


def test_configure_sqlite_connection_rejects_invalid_pragma_values():
    settings = Settings(sqlite_journal_mode="wal; drop table users")

    with pytest.raises(RuntimeError, match="SQLITE_JOURNAL_MODE"):
        database.configure_sqlite_connection(FakeDBAPIConnection(), settings)


class FakeDBAPICursor:
    def __init__(self):
        self.statements: list[str] = []
        self.closed = False

    def execute(self, statement: str):
        self.statements.append(statement)

    def close(self):
        self.closed = True


class FakeDBAPIConnection:
    def __init__(self):
        self.cursor_obj = FakeDBAPICursor()

    def cursor(self):
        return self.cursor_obj
