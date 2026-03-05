import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from app.services.ai_text import AITextService


class TestAITextService:
    """AI 文本服务测试"""

    def test_parse_story_response_with_choices(self):
        """测试解析带分支选项的故事响应"""
        service = AITextService()
        content = """【故事内容】
你在咖啡馆看到他坐在角落，阳光洒在他身上。

【分支选项】
A. 走过去打招呼
B. 假装没看到
C. 找个位置偷偷观察"""

        result = service._parse_story_response(content)

        assert "咖啡馆" in result["content"]
        assert len(result["choices"]) == 3
        assert "A. 走过去打招呼" in result["choices"][0]["label"]

    def test_parse_story_response_without_choices(self):
        """测试解析没有分支选项的故事响应"""
        service = AITextService()
        content = "这是一段没有分支选项的故事内容。"

        result = service._parse_story_response(content)

        assert result["content"] == content
        # 应该有默认选项
        assert len(result["choices"]) == 2

    def test_parse_story_response_alternative_format(self):
        """测试解析替代格式的分支选项"""
        service = AITextService()
        content = """故事内容在这里。

【选择】
A. 选项一
B. 选项二"""

        result = service._parse_story_response(content)

        assert len(result["choices"]) == 2

    @pytest.mark.asyncio
    async def test_auto_complete_character_mock(self):
        """测试角色自动补全（Mock API）"""
        service = AITextService()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{
                "message": {
                    "content": '{"gender": "男", "age": 28, "identity": "律师", "appearance": "身高180", "personality": ["高冷"], "speaking_style": "冷淡", "background": "精英律师"}'
                }
            }]
        }
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.post = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            result = await service.auto_complete_character(
                name="何以琛",
                inspiration="何以笙箫默"
            )

            assert result["gender"] == "男"
            assert result["age"] == 28
            assert "高冷" in result["personality"]

    @pytest.mark.asyncio
    async def test_auto_complete_character_json_in_markdown(self):
        """测试解析 markdown 代码块中的 JSON"""
        service = AITextService()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{
                "message": {
                    "content": '```json\n{"gender": "女", "age": 25, "identity": "医生", "appearance": "长发", "personality": ["温柔"], "speaking_style": "温和", "background": "外科医生"}\n```'
                }
            }]
        }
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.post = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            result = await service.auto_complete_character(gender="女")

            assert result["gender"] == "女"
            assert result["age"] == 25

    @pytest.mark.asyncio
    async def test_auto_complete_character_invalid_json(self):
        """测试处理无效 JSON 响应"""
        service = AITextService()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{
                "message": {
                    "content": "这不是有效的 JSON"
                }
            }]
        }
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.post = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            result = await service.auto_complete_character(name="测试")

            # 应该返回默认值
            assert "gender" in result
            assert "age" in result
