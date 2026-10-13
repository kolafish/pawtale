import pytest

from app.services import ai_factory
from app.services.ai_access import reset_request_ai_model_scope, set_request_ai_model_scope
from app.services.ai_text import DeepSeekTextService
from app.services.ai_volc import VolcArkTextService

VOLC_DEFAULT_MODEL = ai_factory.settings.volc_ark_model


@pytest.mark.parametrize(
    ("model", "expected_type", "expected_model"),
    [
        ("deepseek", DeepSeekTextService, "deepseek-flash"),
        ("deepseek-flash", DeepSeekTextService, "deepseek-flash"),
        ("deepseek-v4-flash", DeepSeekTextService, "deepseek-flash"),
        ("deepseek-v4-pro", DeepSeekTextService, "deepseek-v4-pro"),
        ("doubao-seed-2-0-pro-260215", VolcArkTextService, VOLC_DEFAULT_MODEL),
        ("volc-doubao-pro", VolcArkTextService, VOLC_DEFAULT_MODEL),
    ],
)
def test_get_ai_service_resolves_supported_models(model, expected_type, expected_model):
    service = ai_factory.get_ai_service(model)

    assert isinstance(service, expected_type)
    assert service.model_name == expected_model


@pytest.mark.parametrize(
    ("default_model", "expected_type", "expected_model"),
    [
        ("deepseek", DeepSeekTextService, "deepseek-flash"),
        ("deepseek-flash", DeepSeekTextService, "deepseek-flash"),
        ("deepseek-v4-pro", DeepSeekTextService, "deepseek-v4-pro"),
        ("doubao-seed-2-0-pro-260215", VolcArkTextService, VOLC_DEFAULT_MODEL),
    ],
)
def test_get_ai_service_uses_configured_default(monkeypatch, default_model, expected_type, expected_model):
    monkeypatch.setattr("app.services.ai_factory.settings.default_ai_model", default_model)

    service = ai_factory.get_ai_service()

    assert isinstance(service, expected_type)
    assert service.model_name == expected_model


def test_get_ai_service_rejects_invalid_model():
    with pytest.raises(
        ai_factory.UnsupportedAIModelError,
        match="不支持的 AI 模型",
    ):
        ai_factory.get_ai_service("invalid-model")


def test_public_request_scope_forces_the_default_model():
    token = set_request_ai_model_scope(is_admin=False)
    try:
        assert ai_factory.get_ai_service("deepseek-v4-pro").model_name == "deepseek-flash"
    finally:
        reset_request_ai_model_scope(token)


def test_retired_model_is_rejected_for_admins():
    token = set_request_ai_model_scope(is_admin=True)
    try:
        with pytest.raises(ai_factory.UnsupportedAIModelError):
            ai_factory.get_ai_service("claude-opus-4-8")
    finally:
        reset_request_ai_model_scope(token)


@pytest.mark.parametrize("saved_model", ["claude-fable-5", "claude-opus-4-8", "claude-sonnet-4", "claude-sonnet-4-6"])
def test_retired_project_model_moves_to_default(saved_model):
    from app.config import DEFAULT_AI_TEXT_MODEL
    from app.models.projects import Project
    from app.services.memoir.project_ai_preferences import select_project_ai_model

    project = Project(metadata_json={"ai_model": saved_model})
    assert select_project_ai_model(project, None) == DEFAULT_AI_TEXT_MODEL
    assert project.metadata_json["ai_model"] == DEFAULT_AI_TEXT_MODEL
