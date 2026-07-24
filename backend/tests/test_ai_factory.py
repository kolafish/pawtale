import pytest

from app.services import ai_factory
from app.services.ai_claude import ClaudeTextService
from app.services.ai_text import DeepSeekTextService
from app.services.ai_volc import VolcArkTextService

VOLC_DEFAULT_MODEL = ai_factory.settings.volc_ark_model


@pytest.mark.parametrize(
    ("model", "expected_type", "expected_model"),
    [
        ("deepseek", DeepSeekTextService, "deepseek-v4-flash"),
        ("deepseek-v4-flash", DeepSeekTextService, "deepseek-v4-flash"),
        ("deepseek-v4-pro", DeepSeekTextService, "deepseek-v4-pro"),
        ("doubao-seed-2-0-pro-260215", VolcArkTextService, VOLC_DEFAULT_MODEL),
        ("volc-doubao-pro", VolcArkTextService, VOLC_DEFAULT_MODEL),
        ("claude-fable-5", ClaudeTextService, "claude-fable-5"),
        ("claude-opus-4-8", ClaudeTextService, "claude-opus-4-8"),
        ("claude-sonnet-4", ClaudeTextService, "claude-sonnet-4-6"),
    ],
)
def test_get_ai_service_resolves_supported_models(model, expected_type, expected_model):
    service = ai_factory.get_ai_service(model)

    assert isinstance(service, expected_type)
    assert service.model_name == expected_model


@pytest.mark.parametrize(
    ("default_model", "expected_type", "expected_model"),
    [
        ("claude-fable-5", ClaudeTextService, "claude-fable-5"),
        ("claude-opus-4-8", ClaudeTextService, "claude-opus-4-8"),
        ("deepseek", DeepSeekTextService, "deepseek-v4-flash"),
        ("deepseek-v4-pro", DeepSeekTextService, "deepseek-v4-pro"),
        ("doubao-seed-2-0-pro-260215", VolcArkTextService, VOLC_DEFAULT_MODEL),
    ],
)
def test_get_ai_service_uses_configured_default(monkeypatch, default_model, expected_type, expected_model):
    monkeypatch.setattr("app.services.ai_factory.settings.default_ai_model", default_model)

    service = ai_factory.get_ai_service()

    assert isinstance(service, expected_type)
    assert service.model_name == expected_model


def test_get_ai_service_invalid_model_falls_back_to_configured_default(monkeypatch):
    monkeypatch.setattr("app.services.ai_factory.settings.default_ai_model", "claude-fable-5")

    service = ai_factory.get_ai_service("invalid-model")

    assert isinstance(service, ClaudeTextService)
    assert service.model_name == "claude-fable-5"


@pytest.mark.parametrize(
    ("payload", "expected_text"),
    [
        (
            {"choices": [{"message": {"content": '```json\n{"characters": [{"name": "林晚"}]}\n```'}}]},
            "characters",
        ),
        (
            {"choices": [{"message": {"content": [{"type": "text", "text": '{"characters": [{"name": "周彻"}]}'}]}}]},
            "周彻",
        ),
        (
            {
                "content": [
                    {"type": "thinking", "text": "分析中"},
                    {"type": "text", "text": '{"characters": [{"name": "顾言"}]}'},
                ]
            },
            "顾言",
        ),
    ],
)
def test_claude_extract_response_text_accepts_proxy_shapes(payload, expected_text):
    service = ClaudeTextService(model="claude-fable-5")

    assert expected_text in service._extract_response_text(payload)
