import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from app.services.ai_factory import get_ai_service
from app.services.ai_claude import ClaudeTextService
from app.services.ai_text import DeepSeekTextService


class TestMultiModelSupport:
    """多模型支持测试"""

    def test_get_deepseek_service(self):
        """测试获取 DeepSeek 服务"""
        service = get_ai_service("deepseek")
        assert isinstance(service, DeepSeekTextService)

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

    def test_get_default_service(self):
        """测试获取默认服务"""
        service = get_ai_service()
        assert isinstance(service, DeepSeekTextService)

    def test_get_invalid_model(self):
        """测试获取无效模型时返回默认服务"""
        service = get_ai_service("invalid-model")
        assert isinstance(service, DeepSeekTextService)


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


class TestOutlineGuidedCreation:
    """引导式创建测试"""

    @pytest.mark.asyncio
    async def test_guided_create_endpoint_exists(self, client):
        """测试引导式创建端点存在"""
        with patch("app.routers.outlines.get_ai_service") as mock_get_service:
            mock_service = AsyncMock()
            mock_service.generate_outline_draft = AsyncMock(return_value={
                "title": "测试大纲",
                "central_conflict": "核心冲突",
                "plot_hints": ["提示1", "提示2"],
                "mood": "甜",
                "ending_type": "HE",
                "writing_style": "细腻",
                "forbidden_elements": [],
            })
            mock_get_service.return_value = mock_service

            response = await client.post(
                "/api/outlines/guided-create",
                json={
                    "story_goal": "写一个甜蜜的爱情故事",
                    "character_tension": "霸道总裁与平凡女孩",
                    "central_conflict": "身份差距带来的误解与考验",
                    "ai_model": "deepseek",
                }
            )

            assert response.status_code == 200
            data = response.json()
            assert data["title"] == "测试大纲"
            assert data["central_conflict"] == "核心冲突"
            assert data["story_goal"] == "写一个甜蜜的爱情故事"
            assert data["character_tension"] == "霸道总裁与平凡女孩"

    @pytest.mark.asyncio
    async def test_guided_create_with_claude(self, client):
        """测试使用 Claude 模型的引导式创建"""
        with patch("app.routers.outlines.get_ai_service") as mock_get_service:
            mock_service = AsyncMock()
            mock_service.generate_outline_draft = AsyncMock(return_value={
                "title": "Claude 生成的大纲",
                "central_conflict": "冲突",
                "plot_hints": ["提示"],
                "mood": "虐",
                "ending_type": "BE",
                "writing_style": "诗意",
                "forbidden_elements": [],
            })
            mock_get_service.return_value = mock_service

            response = await client.post(
                "/api/outlines/guided-create",
                json={
                    "story_goal": "写一个虐心故事",
                    "character_tension": "相爱不能相守",
                    "central_conflict": "命运的捉弄与无奈的选择",
                    "ai_model": "claude-opus-4",
                }
            )

            assert response.status_code == 200
            data = response.json()
            assert data["title"] == "Claude 生成的大纲"
            # 验证调用了正确的模型
            mock_get_service.assert_called_with("claude-opus-4")


class TestStoryWithAIModel:
    """故事生成时的 AI 模型选择测试"""

    @pytest.mark.asyncio
    async def test_generate_story_with_model_selection(self, client):
        """测试生成故事时可以选择 AI 模型"""
        # 创建角色
        char_response = await client.post("/api/characters", json={"name": "测试角色"})
        char_id = char_response.json()["id"]

        # 初始化场景
        await client.post("/api/scenes/init-presets")
        scenes = (await client.get("/api/scenes")).json()
        scene_id = scenes[0]["id"]

        with patch("app.routers.stories.get_ai_service") as mock_get_service:
            mock_service = AsyncMock()
            mock_service.generate_story = AsyncMock(return_value={
                "content": "故事内容",
                "choices": [{"label": "选项A", "hint": "提示"}],
            })
            mock_get_service.return_value = mock_service

            response = await client.post(
                "/api/stories/generate",
                json={
                    "character_ids": [char_id],
                    "scene_id": scene_id,
                    "mood": "甜",
                    "ai_model": "claude-sonnet-4",
                }
            )

            assert response.status_code == 200
            # 验证使用了指定的模型
            mock_get_service.assert_called_with("claude-sonnet-4")


class TestOutlineFields:
    """大纲新字段测试"""

    @pytest.mark.asyncio
    async def test_create_outline_with_new_fields(self, client):
        """测试创建大纲时包含新字段"""
        response = await client.post(
            "/api/outlines",
            json={
                "title": "完整大纲",
                "mood": "甜",
                "ai_model": "claude-opus-4",
                "story_goal": "创作一个温馨的故事",
                "character_tension": "青梅竹马的重逢",
                "creation_stage": "draft",
            }
        )

        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "完整大纲"
        assert data["ai_model"] == "claude-opus-4"
        assert data["story_goal"] == "创作一个温馨的故事"
        assert data["character_tension"] == "青梅竹马的重逢"
        assert data["creation_stage"] == "draft"

    @pytest.mark.asyncio
    async def test_update_outline_ai_model(self, client):
        """测试更新大纲的 AI 模型"""
        # 创建大纲
        create_response = await client.post(
            "/api/outlines",
            json={"title": "测试大纲", "mood": "甜", "ai_model": "deepseek"}
        )
        outline_id = create_response.json()["id"]

        # 更新模型
        update_response = await client.put(
            f"/api/outlines/{outline_id}",
            json={"ai_model": "claude-sonnet-4"}
        )

        assert update_response.status_code == 200
        data = update_response.json()
        assert data["ai_model"] == "claude-sonnet-4"
