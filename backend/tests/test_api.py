import pytest
from unittest.mock import AsyncMock, patch
from app.services.ai_scene import AISceneParseError, AISceneProviderError, AISceneTimeoutError


class TestCharacterAPI:
    """角色 API 测试"""

    async def test_get_characters_empty(self, client):
        """测试获取空角色列表"""
        response = await client.get("/api/characters")
        assert response.status_code == 200
        assert response.json() == []

    async def test_create_character(self, client):
        """测试创建角色"""
        character_data = {
            "name": "陆辰",
            "gender": "男",
            "age": 28,
            "identity": "CEO",
            "personality": ["高冷", "深情"],
        }
        response = await client.post("/api/characters", json=character_data)
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "陆辰"
        assert data["gender"] == "男"
        assert data["age"] == 28
        assert data["identity"] == "CEO"
        assert "高冷" in data["personality"]
        assert "id" in data

    async def test_get_character_by_id(self, client):
        """测试获取单个角色"""
        # 先创建
        create_response = await client.post("/api/characters", json={"name": "测试角色"})
        character_id = create_response.json()["id"]

        # 再获取
        response = await client.get(f"/api/characters/{character_id}")
        assert response.status_code == 200
        assert response.json()["name"] == "测试角色"

    async def test_get_character_not_found(self, client):
        """测试获取不存在的角色"""
        response = await client.get("/api/characters/nonexistent-id")
        assert response.status_code == 404

    async def test_update_character(self, client):
        """测试更新角色"""
        # 先创建
        create_response = await client.post("/api/characters", json={"name": "原名"})
        character_id = create_response.json()["id"]

        # 再更新
        response = await client.put(
            f"/api/characters/{character_id}",
            json={"name": "新名字", "age": 30}
        )
        assert response.status_code == 200
        assert response.json()["name"] == "新名字"
        assert response.json()["age"] == 30

    async def test_delete_character(self, client):
        """测试删除角色"""
        # 先创建
        create_response = await client.post("/api/characters", json={"name": "待删除"})
        character_id = create_response.json()["id"]

        # 删除
        response = await client.delete(f"/api/characters/{character_id}")
        assert response.status_code == 200

        # 确认已删除
        get_response = await client.get(f"/api/characters/{character_id}")
        assert get_response.status_code == 404

    async def test_auto_complete_route_exists(self, client):
        """测试 AI 补全路由存在且可访问"""
        # Mock AI 服务
        with patch("app.services.ai_factory.get_ai_service") as mock_get_service:
            mock_service = AsyncMock()
            mock_service.auto_complete_character = AsyncMock(return_value={
                "gender": "男",
                "age": 28,
                "identity": "律师",
                "appearance": "身高180cm，短发",
                "personality": ["高冷", "深情"],
                "speaking_style": "言简意赅",
                "background": "知名律所合伙人",
            })
            mock_get_service.return_value = mock_service

            response = await client.post(
                "/api/characters/auto-complete",
                json={"name": "何以琛", "inspiration": "何以笙箫默"}
            )

            assert response.status_code == 200
            data = response.json()
            assert data["gender"] == "男"
            assert data["age"] == 28
            assert "高冷" in data["personality"]


class TestSceneAPI:
    """场景 API 测试"""

    async def test_get_scenes_empty(self, client):
        """测试获取空场景列表"""
        response = await client.get("/api/scenes")
        assert response.status_code == 200
        assert response.json() == []

    async def test_init_preset_scenes(self, client):
        """测试初始化预设场景"""
        response = await client.post("/api/scenes/init-presets")
        assert response.status_code == 200

        # 再次获取应该有场景
        scenes_response = await client.get("/api/scenes")
        assert scenes_response.status_code == 200
        scenes = scenes_response.json()
        assert len(scenes) > 0
        # 检查预设场景
        scene_names = [s["name"] for s in scenes]
        assert "咖啡馆偶遇" in scene_names

    async def test_create_custom_scene(self, client):
        """测试创建自定义场景"""
        scene_data = {
            "name": "图书馆相遇",
            "category": "初遇",
            "description": "在安静的图书馆，你们的手同时伸向同一本书",
            "location": "市立图书馆",
            "mood_keywords": ["安静", "心动"],
        }
        response = await client.post("/api/scenes", json=scene_data)
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "图书馆相遇"
        assert data["is_preset"] == False

    async def test_get_scene_by_id(self, client):
        """测试获取单个场景"""
        create_response = await client.post("/api/scenes", json={
            "name": "夜跑邂逅",
            "category": "暧昧",
            "description": "夜跑时在公园相遇",
        })
        scene_id = create_response.json()["id"]

        response = await client.get(f"/api/scenes/{scene_id}")
        assert response.status_code == 200
        assert response.json()["name"] == "夜跑邂逅"

    async def test_update_scene(self, client):
        """测试更新场景"""
        create_response = await client.post("/api/scenes", json={
            "name": "旧场景",
            "category": "初遇",
            "description": "旧描述",
        })
        scene_id = create_response.json()["id"]

        response = await client.put(
            f"/api/scenes/{scene_id}",
            json={"name": "新场景", "description": "新描述", "category": "甜蜜"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "新场景"
        assert data["description"] == "新描述"
        assert data["category"] == "甜蜜"

    async def test_delete_scene(self, client):
        """测试删除场景"""
        create_response = await client.post("/api/scenes", json={
            "name": "待删除场景",
            "category": "虐心",
            "description": "待删除描述",
        })
        scene_id = create_response.json()["id"]

        response = await client.delete(f"/api/scenes/{scene_id}")
        assert response.status_code == 200

        get_response = await client.get(f"/api/scenes/{scene_id}")
        assert get_response.status_code == 404

    async def test_ai_generate_scene_parse_error_mapping(self, client):
        """测试场景 AI 解析错误映射为 502"""
        with patch("app.routers.scenes.ai_scene_service.generate_scene_candidates") as mock_generate:
            mock_generate.side_effect = AISceneParseError("模型输出无效")
            response = await client.post(
                "/api/scenes/ai-generate",
                json={
                    "category": "初遇",
                    "mood_keywords": ["雨夜"],
                    "count": 3,
                    "ai_model": "claude-opus-4",
                },
            )

        assert response.status_code == 502
        assert "模型返回格式异常" in response.json()["detail"]

    async def test_ai_generate_scene_timeout_error_mapping(self, client):
        """测试场景 AI 超时映射为 504"""
        with patch("app.routers.scenes.ai_scene_service.generate_scene_candidates") as mock_generate:
            mock_generate.side_effect = AISceneTimeoutError("请求超时")
            response = await client.post(
                "/api/scenes/ai-generate",
                json={
                    "category": "初遇",
                    "mood_keywords": ["雨夜"],
                    "count": 3,
                    "ai_model": "deepseek",
                },
            )

        assert response.status_code == 504
        assert "模型响应超时" in response.json()["detail"]

    async def test_ai_generate_scene_provider_error_mapping(self, client):
        """测试场景 AI 上游错误映射为 502"""
        with patch("app.routers.scenes.ai_scene_service.generate_scene_candidates") as mock_generate:
            mock_generate.side_effect = AISceneProviderError("上游服务异常")
            response = await client.post(
                "/api/scenes/ai-generate",
                json={
                    "category": "初遇",
                    "mood_keywords": ["雨夜"],
                    "count": 3,
                    "ai_model": "deepseek",
                },
            )

        assert response.status_code == 502
        assert "上游模型服务异常" in response.json()["detail"]


class TestStoryAPI:
    """故事 API 测试"""

    async def test_get_stories_empty(self, client):
        """测试获取空故事列表"""
        response = await client.get("/api/stories")
        assert response.status_code == 200
        assert response.json() == []

    async def test_generate_story_missing_character(self, client):
        """测试生成故事时角色不存在"""
        # 先初始化场景
        await client.post("/api/scenes/init-presets")
        scenes = (await client.get("/api/scenes")).json()
        scene_id = scenes[0]["id"]

        response = await client.post("/api/stories/generate", json={
            "character_ids": ["nonexistent-id"],
            "scene_id": scene_id,
            "mood": "甜",
        })
        assert response.status_code == 404

    async def test_generate_story_missing_scene(self, client):
        """测试生成故事时场景不存在"""
        # 先创建角色
        char_response = await client.post("/api/characters", json={"name": "测试角色"})
        char_id = char_response.json()["id"]

        response = await client.post("/api/stories/generate", json={
            "character_ids": [char_id],
            "scene_id": "nonexistent-scene",
            "mood": "甜",
        })
        assert response.status_code == 404


class TestHealthCheck:
    """健康检查测试"""

    async def test_root(self, client):
        """测试根路径"""
        response = await client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Pawtale API"
        assert data["slogan"] == "是我的CP呀"

    async def test_health(self, client):
        """测试健康检查"""
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
