from app.config import Settings
from app.services.ai_timeouts import (
    AITextTimeoutProfile,
    ai_text_timeout_seconds,
    ai_user_error_message,
)


def test_ai_text_timeout_profiles_use_shared_settings():
    settings = Settings(
        ai_text_realtime_timeout_seconds=12,
        ai_text_interactive_timeout_seconds=45,
        ai_text_generation_timeout_seconds=150,
    )

    assert ai_text_timeout_seconds(
        AITextTimeoutProfile.REALTIME,
        settings=settings,
    ) == 12
    assert ai_text_timeout_seconds(
        AITextTimeoutProfile.INTERACTIVE,
        settings=settings,
    ) == 45
    assert ai_text_timeout_seconds(
        AITextTimeoutProfile.GENERATION,
        settings=settings,
    ) == 150


def test_ai_user_error_message_does_not_expose_provider_english():
    assert ai_user_error_message(
        RuntimeError("upstream unavailable"),
        "模型服务暂时不可用",
    ) == "模型服务暂时不可用"
    assert ai_user_error_message(
        RuntimeError("模型返回结构无效"),
        "模型服务暂时不可用",
    ) == "模型返回结构无效"
