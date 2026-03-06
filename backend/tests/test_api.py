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

    async def test_ai_generate_scene_debug_true_returns_debug_info(self, client):
        """测试场景 AI debug=true 时返回 debug_info"""
        with patch("app.routers.scenes.ai_scene_service.generate_scene_candidates") as mock_generate:
            mock_generate.return_value = (
                [
                    {
                        "name": "雨夜旧街",
                        "description": "雨夜中的旧街，适合重逢与试探。",
                        "mood_keywords": ["雨夜", "重逢"],
                    }
                ],
                {
                    "request": {"provider": "claude", "model": "claude-opus-4-20250514"},
                    "response": {"raw_text": "[]", "raw_json": {"content": []}},
                    "meta": {"truncated": False},
                },
            )
            response = await client.post(
                "/api/scenes/ai-generate",
                json={
                    "category": "初遇",
                    "mood_keywords": ["雨夜"],
                    "count": 1,
                    "ai_model": "claude-opus-4",
                    "debug": True,
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert "debug_info" in data
        assert data["debug_info"]["request"]["provider"] == "claude"

    async def test_ai_generate_scene_debug_false_hides_debug_info(self, client):
        """测试场景 AI debug=false 时不返回 debug_info"""
        with patch("app.routers.scenes.ai_scene_service.generate_scene_candidates") as mock_generate:
            mock_generate.return_value = (
                [
                    {
                        "name": "雨夜旧街",
                        "description": "雨夜中的旧街，适合重逢与试探。",
                        "mood_keywords": ["雨夜", "重逢"],
                    }
                ],
                None,
            )
            response = await client.post(
                "/api/scenes/ai-generate",
                json={
                    "category": "初遇",
                    "mood_keywords": ["雨夜"],
                    "count": 1,
                    "ai_model": "claude-opus-4",
                    "debug": False,
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert "debug_info" not in data


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

    async def test_generate_story_debug_true_returns_debug_info(self, client):
        """测试故事生成 debug=true 时返回 debug_info"""
        char_response = await client.post("/api/characters", json={"name": "测试角色"})
        char_id = char_response.json()["id"]

        scene_response = await client.post(
            "/api/scenes",
            json={"name": "测试场景", "category": "初遇", "description": "测试描述"},
        )
        scene_id = scene_response.json()["id"]

        with patch("app.routers.stories.get_ai_service") as mock_get_service:
            mock_service = AsyncMock()
            mock_service.generate_story = AsyncMock(return_value={
                "title": "第一章",
                "content": "故事内容",
                "word_count": 4,
                "choices": [{"label": "A. 继续发展", "next_node": None}],
                "debug_info": {
                    "request": {"provider": "deepseek", "model": "deepseek-chat"},
                    "response": {"raw_text": "原始内容"},
                    "meta": {"truncated": False},
                },
            })
            mock_get_service.return_value = mock_service

            response = await client.post(
                "/api/stories/generate",
                json={
                    "character_ids": [char_id],
                    "scene_id": scene_id,
                    "mood": "甜",
                    "writing_style": "细腻",
                    "chapter_length": "中",
                    "debug": True,
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert "debug_info" in data
        assert data["debug_info"]["request"]["provider"] == "deepseek"

    async def test_generate_story_debug_false_hides_debug_info(self, client):
        """测试故事生成 debug=false 时不返回 debug_info"""
        char_response = await client.post("/api/characters", json={"name": "测试角色"})
        char_id = char_response.json()["id"]

        scene_response = await client.post(
            "/api/scenes",
            json={"name": "测试场景", "category": "初遇", "description": "测试描述"},
        )
        scene_id = scene_response.json()["id"]

        with patch("app.routers.stories.get_ai_service") as mock_get_service:
            mock_service = AsyncMock()
            mock_service.generate_story = AsyncMock(return_value={
                "title": "第一章",
                "content": "故事内容",
                "word_count": 4,
                "choices": [{"label": "A. 继续发展", "next_node": None}],
            })
            mock_get_service.return_value = mock_service

            response = await client.post(
                "/api/stories/generate",
                json={
                    "character_ids": [char_id],
                    "scene_id": scene_id,
                    "mood": "甜",
                    "writing_style": "细腻",
                    "chapter_length": "中",
                    "debug": False,
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert "debug_info" not in data

    async def test_create_story_workspace(self, client):
        """测试创建编辑器优先工作区"""
        response = await client.post(
            "/api/stories/workspace",
            json={
                "title": "编辑器模式测试",
                "content": "这是我自己先写的开头。",
                "ai_model": "deepseek",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "编辑器模式测试"
        assert data["editor_content"] == "这是我自己先写的开头。"
        assert data["story_id"]
        assert data["extracted_characters"] == []

    async def test_workspace_ai_continue(self, client):
        """测试工作区 AI 续写"""
        create_resp = await client.post(
            "/api/stories/workspace",
            json={
                "title": "续写测试",
                "content": "窗外下着雨，她站在便利店门口。",
            },
        )
        story_id = create_resp.json()["story_id"]

        with patch("app.routers.stories.get_ai_service") as mock_get_service:
            mock_service = AsyncMock()
            mock_service.generate_story = AsyncMock(return_value={
                "content": "他把伞递过来，却没说一句话。",
                "choices": [{"label": "A. 接过伞", "next_node": None}],
            })
            mock_get_service.return_value = mock_service

            response = await client.post(
                f"/api/stories/{story_id}/workspace/ai-continue",
                json={"instruction": "继续写一段重逢感", "append": True},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["generated_chunk"] == "他把伞递过来，却没说一句话。"
        assert "他把伞递过来" in data["editor_content"]
        assert len(data["choices"]) == 1

    async def test_workspace_extract_and_manual_update(self, client):
        """测试工作区自动提炼与手工修改"""
        create_resp = await client.post(
            "/api/stories/workspace",
            json={
                "title": "提炼测试",
                "content": "林晚在天台遇见周彻，两人因旧事争执。",
            },
        )
        story_id = create_resp.json()["story_id"]

        with patch("app.routers.stories.get_ai_service") as mock_get_service:
            mock_service = AsyncMock()
            mock_service.extract_story_context = AsyncMock(return_value={
                "extracted_characters": [
                    {"name": "林晚", "role_hint": "主角", "traits": ["倔强"], "summary": "摄影师"}
                ],
                "extracted_scenes": [
                    {"name": "天台", "description": "夜风很大", "keywords": ["夜晚", "争执"]}
                ],
                "extracted_outline_points": [
                    {"stage": "开端", "summary": "两人重逢并发生争执"}
                ],
                "extraction_notes": ["可能存在时间线跳跃"],
            })
            mock_get_service.return_value = mock_service

            extract_resp = await client.post(
                f"/api/stories/{story_id}/workspace/extract",
                json={"debug": False},
            )

        assert extract_resp.status_code == 200
        extracted = extract_resp.json()
        assert extracted["extracted_characters"][0]["name"] == "林晚"
        assert extracted["extracted_scenes"][0]["name"] == "天台"

        update_resp = await client.put(
            f"/api/stories/{story_id}/workspace/extracted",
            json={
                "extracted_characters": [
                    {"name": "林晚", "role_hint": "主角", "traits": ["冷静"], "summary": "纪录片导演"}
                ],
                "extraction_notes": ["已手工校准角色职业"],
            },
        )
        assert update_resp.status_code == 200
        updated = update_resp.json()
        assert updated["extracted_characters"][0]["traits"] == ["冷静"]
        assert updated["extraction_notes"] == ["已手工校准角色职业"]

    async def test_workspace_extract_debug_true_returns_fallback_debug_info(self, client):
        """测试工作区提炼 debug=true 时即使服务未返回 debug_info 也有兜底信息"""
        create_resp = await client.post(
            "/api/stories/workspace",
            json={
                "title": "提炼调试测试",
                "content": "林晚在天台遇见周彻，两人因旧事争执。",
            },
        )
        story_id = create_resp.json()["story_id"]

        with patch("app.routers.stories.get_ai_service") as mock_get_service:
            mock_service = AsyncMock()
            mock_service.extract_story_context = AsyncMock(return_value={
                "extracted_characters": [{"name": "林晚"}],
                "extracted_scenes": [{"name": "天台"}],
                "extracted_outline_points": [],
                "extraction_notes": [],
            })
            mock_get_service.return_value = mock_service

            extract_resp = await client.post(
                f"/api/stories/{story_id}/workspace/extract",
                json={"debug": True},
            )

        assert extract_resp.status_code == 200
        data = extract_resp.json()
        assert "debug_info" in data
        assert data["debug_info"]["request"]["provider"] == "unknown"

    async def test_update_story_scene_id(self, client):
        """测试更新故事关联场景"""
        char_response = await client.post("/api/characters", json={"name": "测试角色"})
        char_id = char_response.json()["id"]
        scene_1 = await client.post(
            "/api/scenes",
            json={"name": "场景一", "category": "初遇", "description": "描述一"},
        )
        scene_2 = await client.post(
            "/api/scenes",
            json={"name": "场景二", "category": "暧昧", "description": "描述二"},
        )
        scene_1_id = scene_1.json()["id"]
        scene_2_id = scene_2.json()["id"]

        with patch("app.routers.stories.get_ai_service") as mock_get_service:
            mock_service = AsyncMock()
            mock_service.generate_story = AsyncMock(return_value={
                "title": "第一章",
                "content": "故事内容",
                "word_count": 4,
                "choices": [{"label": "A. 继续发展", "next_node": None}],
            })
            mock_get_service.return_value = mock_service

            create_story_resp = await client.post(
                "/api/stories/generate",
                json={
                    "character_ids": [char_id],
                    "scene_id": scene_1_id,
                    "mood": "甜",
                    "writing_style": "细腻",
                    "chapter_length": "中",
                },
            )
        story_id = create_story_resp.json()["story_id"]

        update_resp = await client.put(
            f"/api/stories/{story_id}",
            json={"scene_id": scene_2_id},
        )
        assert update_resp.status_code == 200
        assert update_resp.json()["scene_id"] == scene_2_id


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
