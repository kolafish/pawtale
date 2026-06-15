import pytest
from fastapi import Response

from app import auth
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
