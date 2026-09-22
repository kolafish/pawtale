import logging

from app.services.ai_base import AITextServiceBase
from app.services.ai_access import restrict_requested_ai_model
from app.services.ai_text import DeepSeekTextService
from app.services.ai_volc import VolcArkTextService
from app.config import DEFAULT_AI_TEXT_MODEL, VALID_AI_TEXT_MODELS, get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

DEEPSEEK_FLASH_MODEL = "deepseek-flash"
DEEPSEEK_V4_FLASH_MODEL = "deepseek-v4-flash"
DEEPSEEK_V4_PRO_MODEL = "deepseek-v4-pro"
DEEPSEEK_FLASH_ALIASES = frozenset({
    "deepseek",
    DEEPSEEK_FLASH_MODEL,
    DEEPSEEK_V4_FLASH_MODEL,
})
VOLC_DOUBAO_PRO_MODEL = "doubao-seed-2-0-pro-260215"


class UnsupportedAIModelError(ValueError):
    pass


def ensure_supported_ai_model(model: str) -> str:
    normalized = model.strip().lower()
    if normalized not in VALID_AI_TEXT_MODELS:
        raise UnsupportedAIModelError(f"不支持的 AI 模型：{model}")
    return normalized


class AIServiceFactory:
    """AI服务工厂"""

    @staticmethod
    def _service_for_model(model: str) -> AITextServiceBase:
        if model in DEEPSEEK_FLASH_ALIASES:
            return DeepSeekTextService(model=DEEPSEEK_FLASH_MODEL)
        if model == DEEPSEEK_V4_PRO_MODEL:
            return DeepSeekTextService(model=DEEPSEEK_V4_PRO_MODEL)
        if model in {"volc-doubao-pro", VOLC_DOUBAO_PRO_MODEL}:
            return VolcArkTextService()
        raise UnsupportedAIModelError(f"不支持的 AI 模型：{model}")

    @staticmethod
    def get_service(model: str | None = None) -> AITextServiceBase:
        """
        根据模型名称返回对应的AI服务实例

        Args:
            model: 模型名称 (deepseek / deepseek-flash / deepseek-v4-flash /
                  deepseek-v4-pro /
                  doubao-seed-2-0-pro-260215)
                  如果为None，使用默认模型

        Returns:
            AI服务实例
        """
        requested_model = (model or "").strip() or None
        normalized_model = ensure_supported_ai_model(
            requested_model
            or settings.default_ai_model
            or DEFAULT_AI_TEXT_MODEL
        )
        normalized_model = restrict_requested_ai_model(normalized_model)
        service = AIServiceFactory._service_for_model(normalized_model)
        AIServiceFactory._log_resolution(requested_model, service)
        return service

    @staticmethod
    def _log_resolution(requested_model: str | None, service: AITextServiceBase) -> None:
        logger.info(
            "AI model resolved requested=%s resolved=%s provider=%s",
            requested_model or "<default>",
            service.model_name,
            service.provider_name,
        )


# 便捷函数
def get_ai_service(model: str | None = None) -> AITextServiceBase:
    """获取AI服务实例"""
    return AIServiceFactory.get_service(model)
