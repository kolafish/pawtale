import pytest
from fastapi import Response

from app import auth
from app import database
from app.auth import SESSION_COOKIE_NAME, set_session_cookie
from app.config import (
    DEFAULT_APP_SECRET_KEY,
    DEFAULT_DATABASE_URL,
    Settings,
    is_production_environment,
    validate_runtime_settings,
)

PRODUCTION_DATABASE_URL = "postgresql+asyncpg://pawtale:secret@example.test/pawtale"


def test_validate_runtime_settings_rejects_invalid_app_env():
    settings = Settings(app_env="prod", app_secret_key="x" * 32)

    with pytest.raises(RuntimeError, match="APP_ENV must be one of"):
        validate_runtime_settings(settings)


def test_validate_runtime_settings_rejects_unknown_default_ai_model():
    settings = Settings(default_ai_model="unknown-model")

    with pytest.raises(RuntimeError, match="DEFAULT_AI_MODEL"):
        validate_runtime_settings(settings)


def test_validate_runtime_settings_rejects_unknown_interview_prompt_variant():
    settings = Settings(memoir_interview_prompt_variant="unknown")

    with pytest.raises(RuntimeError, match="MEMOIR_INTERVIEW_PROMPT_VARIANT"):
        validate_runtime_settings(settings)


def test_validate_runtime_settings_rejects_default_production_secret():
    settings = Settings(
        app_env="production",
        app_secret_key=DEFAULT_APP_SECRET_KEY,
        database_url=PRODUCTION_DATABASE_URL,
    )

    with pytest.raises(RuntimeError, match="non-default"):
        validate_runtime_settings(settings)


def test_validate_runtime_settings_rejects_short_production_secret():
    settings = Settings(
        app_env="production",
        app_secret_key="short-secret",
        database_url=PRODUCTION_DATABASE_URL,
    )

    with pytest.raises(RuntimeError, match="at least 32 characters"):
        validate_runtime_settings(settings)


def test_validate_runtime_settings_accepts_strong_production_secret():
    settings = Settings(
        app_env=" Production ",
        app_secret_key="x" * 32,
        database_url=PRODUCTION_DATABASE_URL,
    )

    validate_runtime_settings(settings)
    assert is_production_environment(settings)


def test_session_cookie_is_secure_in_production(monkeypatch):
    settings = Settings(
        app_env="production",
        app_secret_key="x" * 32,
        database_url=PRODUCTION_DATABASE_URL,
    )
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


def test_validate_runtime_settings_rejects_non_postgres_database():
    settings = Settings(database_url="mysql+asyncmy://pawtale@example.test/db")

    with pytest.raises(RuntimeError, match=r"postgresql\+asyncpg"):
        validate_runtime_settings(settings)


def test_validate_runtime_settings_requires_explicit_production_database():
    settings = Settings(
        app_env="production",
        app_secret_key="x" * 32,
        database_url=DEFAULT_DATABASE_URL,
    )

    with pytest.raises(RuntimeError, match="explicitly configured"):
        validate_runtime_settings(settings)


def test_postgres_engine_uses_bounded_health_checked_pool(monkeypatch):
    captured: dict[str, object] = {}
    sentinel = object()

    def fake_create_async_engine(url: str, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(database, "create_async_engine", fake_create_async_engine)
    settings = Settings(
        database_url="postgresql+asyncpg://pawtale@example.test/db",
        database_pool_size=7,
        database_max_overflow=4,
        database_pool_timeout_seconds=12.5,
        database_pool_recycle_seconds=900,
    )

    assert database.create_database_engine(settings) is sentinel
    assert captured == {
        "url": settings.database_url,
        "echo": settings.app_env == "development",
        "pool_pre_ping": True,
        "pool_size": 7,
        "max_overflow": 4,
        "pool_timeout": 12.5,
        "pool_recycle": 900,
    }


def test_database_engine_rejects_non_postgres_url():
    settings = Settings(database_url="mysql+asyncmy://pawtale@example.test/db")

    with pytest.raises(RuntimeError, match=r"postgresql\+asyncpg"):
        database.create_database_engine(settings)


def test_small_concurrency_provider_limit_defaults():
    settings = Settings(_env_file=None)

    assert settings.voice_gateway_max_active_sessions == 10
    assert settings.voice_gateway_max_active_sessions_per_user == 1
    assert settings.ai_text_max_concurrent_requests == 3
    assert settings.deepseek_model == "deepseek-flash"
    assert settings.deepseek_thinking_type == "disabled"
    assert settings.volc_ark_base_url == "https://ark.cn-beijing.volces.com/api/v3"
    assert settings.volc_ark_model == "doubao-seed-2-0-pro-260215"
    assert settings.volc_ark_thinking_type == "disabled"
    assert settings.default_ai_model == "deepseek-flash"
    assert settings.tts_max_concurrent_requests == 3
    assert settings.volc_tts_resource_id == "seed-tts-2.0"
    assert settings.volc_tts_speaker == "zh_female_vv_uranus_bigtts"
