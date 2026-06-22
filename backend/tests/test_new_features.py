from app.services.ai_factory import get_ai_service
from app.services.ai_claude import ClaudeTextService
from app.services.ai_text import DeepSeekTextService
from app.services.ai_volc import VolcArkTextService


class TestMultiModelSupport:
    """多模型支持测试"""

    def test_get_deepseek_service(self):
        """测试获取 DeepSeek 服务"""
        service = get_ai_service("deepseek")
        assert isinstance(service, DeepSeekTextService)
        assert service.model_name == "deepseek-v4-flash"

    def test_get_deepseek_v4_flash_service(self):
        """测试获取 DeepSeek V4 Flash 服务"""
        service = get_ai_service("deepseek-v4-flash")
        assert isinstance(service, DeepSeekTextService)
        assert service.model_name == "deepseek-v4-flash"

    def test_get_deepseek_v4_pro_service(self):
        """测试获取 DeepSeek V4 Pro 服务"""
        service = get_ai_service("deepseek-v4-pro")
        assert isinstance(service, DeepSeekTextService)
        assert service.model_name == "deepseek-v4-pro"

    def test_get_volc_doubao_pro_service(self):
        """测试获取火山方舟豆包 Pro 服务"""
        service = get_ai_service("doubao-seed-2-0-pro-260215")
        assert isinstance(service, VolcArkTextService)
        assert service.model_name == "doubao-seed-2-0-pro-260215"

    def test_get_volc_doubao_pro_alias_service(self):
        """测试获取火山方舟豆包 Pro 别名服务"""
        service = get_ai_service("volc-doubao-pro")
        assert isinstance(service, VolcArkTextService)

    def test_get_claude_opus_service(self):
        """测试获取 Claude Opus 服务"""
        service = get_ai_service("claude-opus-4")
        assert isinstance(service, ClaudeTextService)
        assert service.model == "claude-opus-4-6"

    def test_get_claude_sonnet_service(self):
        """测试获取 Claude Sonnet 服务"""
        service = get_ai_service("claude-sonnet-4")
        assert isinstance(service, ClaudeTextService)
        assert service.model == "claude-sonnet-4-6"

    def test_get_default_service(self, monkeypatch):
        """测试获取默认服务"""
        monkeypatch.setattr("app.services.ai_factory.settings.default_ai_model", "claude-opus-4")

        service = get_ai_service()
        assert isinstance(service, ClaudeTextService)
        assert service.model == "claude-opus-4-6"

    def test_get_invalid_model(self, monkeypatch):
        """测试获取无效模型时返回默认服务"""
        monkeypatch.setattr("app.services.ai_factory.settings.default_ai_model", "claude-opus-4")

        service = get_ai_service("invalid-model")
        assert isinstance(service, ClaudeTextService)
        assert service.model == "claude-opus-4-6"

    def test_default_service_can_be_deepseek(self, monkeypatch):
        """测试默认模型配置为 DeepSeek 时返回 DeepSeek 服务"""
        monkeypatch.setattr("app.services.ai_factory.settings.default_ai_model", "deepseek")

        service = get_ai_service()
        assert isinstance(service, DeepSeekTextService)

    def test_default_service_can_be_deepseek_v4_pro(self, monkeypatch):
        """测试默认模型配置为 DeepSeek V4 Pro 时返回 DeepSeek Pro 服务"""
        monkeypatch.setattr("app.services.ai_factory.settings.default_ai_model", "deepseek-v4-pro")

        service = get_ai_service()
        assert isinstance(service, DeepSeekTextService)
        assert service.model_name == "deepseek-v4-pro"

    def test_default_service_can_be_volc_doubao_pro(self, monkeypatch):
        """测试默认模型配置为火山方舟豆包 Pro 时返回火山方舟服务"""
        monkeypatch.setattr("app.services.ai_factory.settings.default_ai_model", "doubao-seed-2-0-pro-260215")

        service = get_ai_service()
        assert isinstance(service, VolcArkTextService)


class TestClaudeResponseCompatibility:
    """Claude 返回格式兼容性测试"""

    def test_extract_response_text_from_openai_message_content(self):
        service = ClaudeTextService(model="claude-opus-4")
        result = {
            "choices": [
                {
                    "message": {
                        "content": "```json\n{\"characters\": [{\"name\": \"林晚\"}]}\n```"
                    }
                }
            ]
        }
        text = service._extract_response_text(result)
        assert "characters" in text

    def test_extract_response_text_from_openai_content_blocks(self):
        service = ClaudeTextService(model="claude-opus-4")
        result = {
            "choices": [
                {
                    "message": {
                        "content": [
                            {"type": "text", "text": "{\"characters\": [{\"name\": \"周彻\"}]}"}
                        ]
                    }
                }
            ]
        }
        text = service._extract_response_text(result)
        assert "周彻" in text

    def test_extract_response_text_from_anthropic_blocks(self):
        service = ClaudeTextService(model="claude-opus-4")
        result = {
            "content": [
                {"type": "thinking", "text": "分析中"},
                {"type": "text", "text": "{\"characters\": [{\"name\": \"顾言\"}]}"}
            ]
        }
        text = service._extract_response_text(result)
        assert "顾言" in text
