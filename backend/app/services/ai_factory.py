from app.services.ai_base import AITextServiceBase
from app.services.ai_text import DeepSeekTextService
from app.services.ai_claude import ClaudeTextService
from app.config import get_settings

settings = get_settings()


class AIServiceFactory:
    """AI服务工厂"""

    @staticmethod
    def _get_default_service() -> AITextServiceBase:
        default_model = (settings.default_ai_model or "claude-opus-4").lower()

        if default_model == "deepseek":
            return DeepSeekTextService()
        if default_model == "claude-sonnet-4":
            return ClaudeTextService(model="claude-sonnet-4")
        return ClaudeTextService(model="claude-opus-4")

    @staticmethod
    def get_service(model: str | None = None) -> AITextServiceBase:
        """
        根据模型名称返回对应的AI服务实例

        Args:
            model: 模型名称 (deepseek / claude-opus-4 / claude-sonnet-4)
                  如果为None，使用默认模型

        Returns:
            AI服务实例
        """
        if model is None:
            return AIServiceFactory._get_default_service()

        model = model.lower()

        if model == "deepseek":
            return DeepSeekTextService()
        elif model == "claude-opus-4":
            return ClaudeTextService(model="claude-opus-4")
        elif model == "claude-sonnet-4":
            return ClaudeTextService(model="claude-sonnet-4")
        else:
            return AIServiceFactory._get_default_service()


# 便捷函数
def get_ai_service(model: str | None = None) -> AITextServiceBase:
    """获取AI服务实例"""
    return AIServiceFactory.get_service(model)
