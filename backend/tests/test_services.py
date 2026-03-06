import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from app.services.ai_text import AITextService
from app.services.ai_scene import AISceneService, AISceneParseError
from app.services.ai_debug import build_debug_info


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

    @pytest.mark.asyncio
    async def test_extract_story_context_with_fenced_json(self):
        """测试从 markdown json 代码块中提炼上下文"""
        service = AITextService()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{
                "message": {
                    "content": """```json
{
  "characters": [{"name": "林晚", "role_hint": "主角", "traits": ["克制"], "summary": "纪录片导演"}],
  "scenes": [{"name": "旧天台", "description": "夜风很大", "keywords": ["重逢"]}],
  "outline_points": [{"stage": "开端", "summary": "重逢并争执"}],
  "consistency_notes": ["注意时间线"]
}
```"""
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

            result = await service.extract_story_context("正文内容")

        assert result["extracted_characters"][0]["name"] == "林晚"
        assert result["extracted_scenes"][0]["name"] == "旧天台"
        assert result["extracted_outline_points"][0]["stage"] == "开端"
        assert result["extraction_notes"][0] == "注意时间线"


class TestAISceneService:
    """场景 AI 服务测试"""

    @pytest.mark.asyncio
    async def test_claude_extracts_text_from_multiple_blocks(self):
        service = AISceneService()
        service.anthropic_auth_token = "test-token"
        service.anthropic_base_url = "https://example.com"

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "content": [
                {"type": "thinking", "text": "分析中"},
                {
                    "type": "text",
                    "text": '```json\n[{"name":"雨夜旧街","description":"雨夜中两人在旧街重逢，氛围紧张又暧昧。"}]\n```',
                },
            ]
        }
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.post = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            content, _ = await service._call_claude(
                model="claude-opus-4",
                system_prompt="system",
                user_prompt="user",
            )

            assert '"name":"雨夜旧街"' in content

    @pytest.mark.asyncio
    async def test_generate_scene_candidates_supports_candidates_wrapper(self):
        service = AISceneService()
        with patch.object(
            service,
            "_call_deepseek",
            AsyncMock(
                return_value='{"candidates":[{"name":"雨夜旧街","description":"雨夜里的旧街，适合重逢与试探。","mood_keywords":["雨夜","重逢"]}]}'
            ),
        ):
            result, _ = await service.generate_scene_candidates(
                category="暧昧",
                mood_keywords=["雨夜", "重逢"],
                count=3,
                ai_model="deepseek",
            )

        assert len(result) == 1
        assert result[0]["name"] == "雨夜旧街"
        assert "重逢" in result[0]["description"]

    @pytest.mark.asyncio
    async def test_generate_scene_candidates_supports_scenes_wrapper(self):
        service = AISceneService()
        with patch.object(
            service,
            "_call_deepseek",
            AsyncMock(
                return_value='{"scenes":[{"name":"夜色天台","description":"风从夜色里吹来，两人靠近又沉默。"}]}'
            ),
        ):
            result, _ = await service.generate_scene_candidates(
                category="甜蜜",
                mood_keywords=["夜色"],
                count=3,
                ai_model="deepseek",
            )

        assert len(result) == 1
        assert result[0]["name"] == "夜色天台"

    @pytest.mark.asyncio
    async def test_generate_scene_candidates_raises_when_json_invalid(self):
        service = AISceneService()
        with patch.object(service, "_call_deepseek", AsyncMock(return_value="这不是 JSON")):
            with pytest.raises(AISceneParseError):
                await service.generate_scene_candidates(
                    category="初遇",
                    mood_keywords=["意外"],
                    count=3,
                    ai_model="deepseek",
                )


class TestAIDebugHelpers:
    def test_build_debug_info_masks_sensitive_and_truncates(self):
        debug_info = build_debug_info(
            request_info={
                "provider": "deepseek",
                "model": "deepseek-chat",
                "authorization": "Bearer very-secret-token",
                "max_tokens": 3000,
                "nested": {"api_key": "abc123456789"},
            },
            response_text="x" * 9000,
            response_json={"token": "resp-secret-token"},
        )

        assert debug_info["request"]["authorization"] != "Bearer very-secret-token"
        assert "***" in debug_info["request"]["authorization"]
        assert debug_info["request"]["max_tokens"] == 3000
        assert debug_info["request"]["nested"]["api_key"] != "abc123456789"
        assert "***" in debug_info["response"]["raw_json"]["token"]
        assert debug_info["meta"]["truncated"] is True

    @pytest.mark.asyncio
    async def test_generate_scene_candidates_raises_when_missing_required_fields(self):
        service = AISceneService()
        with patch.object(
            service,
            "_call_deepseek",
            AsyncMock(return_value='[{"name":"","description":""}]'),
        ):
            with pytest.raises(AISceneParseError):
                await service.generate_scene_candidates(
                    category="初遇",
                    mood_keywords=["意外"],
                    count=3,
                    ai_model="deepseek",
                )
