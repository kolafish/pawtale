import logging

from app.services.ai_base import AITextServiceBase
from app.services.ai_text import DeepSeekTextService
from app.services.ai_claude import (
    CLAUDE_FABLE_MODEL,
    CLAUDE_OPUS_4_8_MODEL,
    ClaudeTextService,
)
from app.services.ai_volc import VolcArkTextService
from app.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

DEEPSEEK_V4_FLASH_MODEL = "deepseek-v4-flash"
DEEPSEEK_V4_PRO_MODEL = "deepseek-v4-pro"
VOLC_DOUBAO_PRO_MODEL = "doubao-seed-2-0-pro-260215"


class AIServiceFactory:
    """AI服务工厂"""

    @staticmethod
    def _get_default_service() -> AITextServiceBase:
        default_model = (settings.default_ai_model or CLAUDE_FABLE_MODEL).lower()

        if default_model in {"deepseek", DEEPSEEK_V4_FLASH_MODEL}:
            return DeepSeekTextService(model=DEEPSEEK_V4_FLASH_MODEL)
        if default_model == DEEPSEEK_V4_PRO_MODEL:
            return DeepSeekTextService(model=DEEPSEEK_V4_PRO_MODEL)
        if default_model in {"volc-doubao-pro", VOLC_DOUBAO_PRO_MODEL}:
            return VolcArkTextService()
        if default_model == CLAUDE_OPUS_4_8_MODEL:
            return ClaudeTextService(model=CLAUDE_OPUS_4_8_MODEL)
        if default_model == "claude-sonnet-4":
            return ClaudeTextService(model="claude-sonnet-4")
        return ClaudeTextService(model=CLAUDE_FABLE_MODEL)

    @staticmethod
    def get_service(model: str | None = None) -> AITextServiceBase:
        """
        根据模型名称返回对应的AI服务实例

        Args:
            model: 模型名称 (deepseek / deepseek-v4-flash / deepseek-v4-pro /
                  doubao-seed-2-0-pro-260215 / claude-fable-5 /
                  claude-opus-4-8 / claude-sonnet-4)
                  如果为None，使用默认模型

        Returns:
            AI服务实例
        """
        requested_model = (model or "").strip() or None
        if requested_model is None:
            service = AIServiceFactory._get_default_service()
            AIServiceFactory._log_resolution(requested_model, service)
            return service

        normalized_model = requested_model.lower()

        if normalized_model in {"deepseek", DEEPSEEK_V4_FLASH_MODEL}:
            service = DeepSeekTextService(model=DEEPSEEK_V4_FLASH_MODEL)
        elif normalized_model == DEEPSEEK_V4_PRO_MODEL:
            service = DeepSeekTextService(model=DEEPSEEK_V4_PRO_MODEL)
        elif normalized_model in {"volc-doubao-pro", VOLC_DOUBAO_PRO_MODEL}:
            service = VolcArkTextService()
        elif normalized_model == CLAUDE_FABLE_MODEL:
            service = ClaudeTextService(model=CLAUDE_FABLE_MODEL)
        elif normalized_model == CLAUDE_OPUS_4_8_MODEL:
            service = ClaudeTextService(model=CLAUDE_OPUS_4_8_MODEL)
        elif normalized_model == "claude-sonnet-4":
            service = ClaudeTextService(model="claude-sonnet-4")
        else:
            service = AIServiceFactory._get_default_service()
        AIServiceFactory._log_resolution(requested_model, service)
        return service

    @staticmethod
    def _log_resolution(requested_model: str | None, service: AITextServiceBase) -> None:
        logger.info(
            "AI model resolved requested=%s resolved=%s provider=%s",
            requested_model or "<default>",
            service.model_name,
            type(service).__name__,
        )


# 便捷函数
def get_ai_service(model: str | None = None) -> AITextServiceBase:
    """获取AI服务实例"""
    return AIServiceFactory.get_service(model)
