import pytest
from unittest.mock import AsyncMock, patch


def project_payload(title: str = "测试", project_type: str = "novel", **overrides):
    return {"type": project_type, "title": title, **overrides}


class TestProjectAPI:
    """Project CRUD tests."""

    async def test_list_projects_empty(self, client):
        response = await client.get("/api/projects")
        assert response.status_code == 200
        assert response.json() == []

    async def test_create_project(self, client):
        response = await client.post("/api/projects", json=project_payload("我的小说"))
        assert response.status_code == 201
        data = response.json()
        assert data["type"] == "novel"
        assert data["title"] == "我的小说"
        assert len(data["groups"]) == 1
        assert data["groups"][0]["name"] == "第一卷"
        assert len(data["groups"][0]["documents"]) == 1

    async def test_create_project_requires_type(self, client):
        response = await client.post("/api/projects", json={"title": "缺少类型"})
        assert response.status_code == 422

    async def test_non_novel_projects_start_without_novel_template(self, client):
        for project_type in ("article", "memoir"):
            response = await client.post("/api/projects", json=project_payload(f"{project_type} 项目", project_type))
            assert response.status_code == 201
            data = response.json()
            assert data["type"] == project_type
            assert data["groups"] == []
            assert data["references"] == []

    async def test_get_project(self, client):
        create = await client.post("/api/projects", json=project_payload("测试项目"))
        pid = create.json()["id"]
        response = await client.get(f"/api/projects/{pid}")
        assert response.status_code == 200
        data = response.json()
        assert data["type"] == "novel"
        assert data["title"] == "测试项目"

    async def test_update_project(self, client):
        create = await client.post("/api/projects", json=project_payload("原标题"))
        pid = create.json()["id"]
        response = await client.put(f"/api/projects/{pid}", json={"title": "新标题", "word_count_target": 50000})
        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "新标题"
        assert data["word_count_target"] == 50000

    async def test_delete_project(self, client):
        create = await client.post("/api/projects", json=project_payload("将被删除"))
        pid = create.json()["id"]
        response = await client.delete(f"/api/projects/{pid}")
        assert response.status_code == 204
        get_resp = await client.get(f"/api/projects/{pid}")
        assert get_resp.status_code == 404

    async def test_project_not_found(self, client):
        response = await client.get("/api/projects/nonexistent")
        assert response.status_code == 404


class TestGroupAPI:
    """Document group tests."""

    async def _create_project(self, client) -> str:
        resp = await client.post("/api/projects", json=project_payload("测试"))
        return resp.json()["id"]

    async def test_create_group(self, client):
        pid = await self._create_project(client)
        response = await client.post(f"/api/projects/{pid}/groups", json={"name": "第二卷"})
        assert response.status_code == 201
        assert response.json()["name"] == "第二卷"

    async def test_update_group(self, client):
        pid = await self._create_project(client)
        proj = (await client.get(f"/api/projects/{pid}")).json()
        gid = proj["groups"][0]["id"]
        response = await client.put(f"/api/projects/{pid}/groups/{gid}", json={"name": "序章"})
        assert response.status_code == 200
        assert response.json()["name"] == "序章"

    async def test_delete_group(self, client):
        pid = await self._create_project(client)
        resp = await client.post(f"/api/projects/{pid}/groups", json={"name": "临时分组"})
        gid = resp.json()["id"]
        del_resp = await client.delete(f"/api/projects/{pid}/groups/{gid}")
        assert del_resp.status_code == 204

    async def test_reorder_groups(self, client):
        pid = await self._create_project(client)
        proj = (await client.get(f"/api/projects/{pid}")).json()
        g1_id = proj["groups"][0]["id"]
        resp2 = await client.post(f"/api/projects/{pid}/groups", json={"name": "第二卷"})
        g2_id = resp2.json()["id"]
        response = await client.put(
            f"/api/projects/{pid}/groups/reorder",
            json=[{"id": g2_id, "sort_order": 0}, {"id": g1_id, "sort_order": 1}],
        )
        assert response.status_code == 200


class TestDocumentAPI:
    """Document CRUD tests."""

    async def _create_project_and_group(self, client):
        resp = await client.post("/api/projects", json=project_payload("测试"))
        proj = resp.json()
        return proj["id"], proj["groups"][0]["id"]

    async def test_create_document(self, client):
        pid, gid = await self._create_project_and_group(client)
        response = await client.post(
            f"/api/projects/{pid}/groups/{gid}/documents", json={"name": "第一章"}
        )
        assert response.status_code == 201
        assert response.json()["name"] == "第一章"

    async def test_get_document(self, client):
        pid, gid = await self._create_project_and_group(client)
        proj = (await client.get(f"/api/projects/{pid}")).json()
        did = proj["groups"][0]["documents"][0]["id"]
        response = await client.get(f"/api/projects/{pid}/documents/{did}")
        assert response.status_code == 200
        assert "content" in response.json()

    async def test_update_document_content(self, client):
        pid, gid = await self._create_project_and_group(client)
        proj = (await client.get(f"/api/projects/{pid}")).json()
        did = proj["groups"][0]["documents"][0]["id"]
        response = await client.put(
            f"/api/projects/{pid}/documents/{did}",
            json={"name": "序章", "content": "这是正文内容", "status": "revision"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "序章"
        assert data["content"] == "这是正文内容"
        assert data["status"] == "revision"
        assert data["word_count"] > 0

    async def test_delete_document(self, client):
        pid, gid = await self._create_project_and_group(client)
        resp = await client.post(f"/api/projects/{pid}/groups/{gid}/documents", json={"name": "临时"})
        did = resp.json()["id"]
        del_resp = await client.delete(f"/api/projects/{pid}/documents/{did}")
        assert del_resp.status_code == 204

    async def test_reorder_documents(self, client):
        pid, gid = await self._create_project_and_group(client)
        proj = (await client.get(f"/api/projects/{pid}")).json()
        d1_id = proj["groups"][0]["documents"][0]["id"]
        resp2 = await client.post(f"/api/projects/{pid}/groups/{gid}/documents", json={"name": "第二"})
        d2_id = resp2.json()["id"]
        response = await client.put(
            f"/api/projects/{pid}/documents/reorder",
            json=[
                {"id": d2_id, "group_id": gid, "sort_order": 0},
                {"id": d1_id, "group_id": gid, "sort_order": 1},
            ],
        )
        assert response.status_code == 200


class TestReferenceAPI:
    """Reference item tests."""

    async def _create_project(self, client) -> str:
        resp = await client.post("/api/projects", json=project_payload("测试"))
        return resp.json()["id"]

    async def test_create_reference(self, client):
        pid = await self._create_project(client)
        response = await client.post(
            f"/api/projects/{pid}/references",
            json={"type": "character", "name": "陆辰", "content": "CEO男主"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["type"] == "character"
        assert data["name"] == "陆辰"

    async def test_list_references(self, client):
        pid = await self._create_project(client)
        await client.post(f"/api/projects/{pid}/references", json={"type": "note", "name": "笔记1"})
        await client.post(f"/api/projects/{pid}/references", json={"type": "note", "name": "笔记2"})
        response = await client.get(f"/api/projects/{pid}/references")
        assert response.status_code == 200
        assert len(response.json()) == 2

    async def test_update_reference(self, client):
        pid = await self._create_project(client)
        create = await client.post(
            f"/api/projects/{pid}/references", json={"type": "note", "name": "旧名"}
        )
        rid = create.json()["id"]
        response = await client.put(
            f"/api/projects/{pid}/references/{rid}", json={"name": "新名"}
        )
        assert response.status_code == 200
        assert response.json()["name"] == "新名"

    async def test_delete_reference(self, client):
        pid = await self._create_project(client)
        create = await client.post(
            f"/api/projects/{pid}/references", json={"type": "note", "name": "删我"}
        )
        rid = create.json()["id"]
        response = await client.delete(f"/api/projects/{pid}/references/{rid}")
        assert response.status_code == 204

    async def test_import_global_characters(self, client):
        # Create a global character first
        char_resp = await client.post("/api/characters", json={"name": "全局角色", "gender": "女"})
        char_id = char_resp.json()["id"]
        pid = await self._create_project(client)
        response = await client.post(
            f"/api/projects/{pid}/references/import-global",
            json={"character_ids": [char_id], "scene_ids": []},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["type"] == "character"
        assert data[0]["name"] == "全局角色"
        assert data[0]["metadata_json"]["global_character_id"] == char_id


class TestDocumentReferenceLinks:
    """Document-reference link tests."""

    async def _setup(self, client):
        resp = await client.post("/api/projects", json=project_payload("测试"))
        proj = resp.json()
        pid = proj["id"]
        did = proj["groups"][0]["documents"][0]["id"]
        ref = await client.post(
            f"/api/projects/{pid}/references", json={"type": "note", "name": "笔记"}
        )
        rid = ref.json()["id"]
        return pid, did, rid

    async def test_set_and_get_document_references(self, client):
        pid, did, rid = await self._setup(client)
        # Set references
        resp = await client.put(
            f"/api/projects/{pid}/documents/{did}/references",
            json={"reference_ids": [rid]},
        )
        assert resp.status_code == 200
        # Get references
        resp2 = await client.get(f"/api/projects/{pid}/documents/{did}/references")
        assert resp2.status_code == 200
        data = resp2.json()
        assert len(data) == 1
        assert data[0]["id"] == rid


class TestWritingSession:
    """Writing session tests."""

    async def test_create_and_list_sessions(self, client):
        resp = await client.post("/api/projects", json=project_payload("测试"))
        pid = resp.json()["id"]
        create = await client.post(
            f"/api/projects/{pid}/sessions",
            json={"date": "2026-03-07", "words_written": 1500},
        )
        assert create.status_code == 201
        assert create.json()["words_written"] == 1500

        listing = await client.get(f"/api/projects/{pid}/sessions")
        assert listing.status_code == 200
        assert len(listing.json()) == 1


class TestExport:
    """Export tests."""

    async def test_export_project(self, client):
        resp = await client.post("/api/projects", json=project_payload("导出测试"))
        proj = resp.json()
        pid = proj["id"]
        did = proj["groups"][0]["documents"][0]["id"]
        await client.put(
            f"/api/projects/{pid}/documents/{did}",
            json={"content": "这是测试内容，包含一些中文和English words。"},
        )
        export = await client.get(f"/api/projects/{pid}/export")
        assert export.status_code == 200
        data = export.json()
        assert data["title"] == "导出测试"
        assert data["total_words"] > 0
        assert len(data["sections"]) == 1


class TestAIContinue:
    """AI continue endpoint tests."""

    async def _setup(self, client):
        resp = await client.post("/api/projects", json=project_payload("AI测试"))
        proj = resp.json()
        pid = proj["id"]
        did = proj["groups"][0]["documents"][0]["id"]
        await client.put(
            f"/api/projects/{pid}/documents/{did}",
            json={"content": "窗外下着雨，她站在便利店门口。"},
        )
        return pid, did

    async def test_ai_continue_success(self, client):
        pid, did = await self._setup(client)
        with patch("app.services.projects.ai.get_ai_service") as mock_get_service:
            mock_service = AsyncMock()
            mock_service.generate_story = AsyncMock(return_value={
                "content": "他走过来递了把伞。",
            })
            mock_get_service.return_value = mock_service

            response = await client.post(
                f"/api/projects/{pid}/documents/{did}/ai-continue",
                json={"ai_model": "claude-opus-4"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["document_id"] == did
        assert "他走过来" in data["content"]
        assert data["ai_model"] == "claude-opus-4"

    async def test_ai_continue_with_instruction(self, client):
        pid, did = await self._setup(client)
        with patch("app.services.projects.ai.get_ai_service") as mock_get_service:
            mock_service = AsyncMock()
            mock_service.generate_story = AsyncMock(return_value={
                "content": "突然一辆车停在她面前。",
            })
            mock_get_service.return_value = mock_service

            response = await client.post(
                f"/api/projects/{pid}/documents/{did}/ai-continue",
                json={"instruction": "增加一个意外事件", "ai_model": "deepseek"},
            )

        assert response.status_code == 200
        mock_get_service.assert_called_with("deepseek")

    async def test_ai_continue_empty_document(self, client):
        resp = await client.post("/api/projects", json=project_payload("空文档"))
        proj = resp.json()
        pid = proj["id"]
        did = proj["groups"][0]["documents"][0]["id"]

        response = await client.post(
            f"/api/projects/{pid}/documents/{did}/ai-continue",
            json={},
        )
        assert response.status_code == 400

    async def test_ai_continue_document_not_found(self, client):
        resp = await client.post("/api/projects", json=project_payload("测试"))
        pid = resp.json()["id"]
        response = await client.post(
            f"/api/projects/{pid}/documents/nonexistent/ai-continue",
            json={},
        )
        assert response.status_code == 404


class TestAIRevise:
    """AI revise endpoint tests."""

    async def _setup(self, client):
        resp = await client.post("/api/projects", json=project_payload("修改测试"))
        proj = resp.json()
        pid = proj["id"]
        did = proj["groups"][0]["documents"][0]["id"]
        await client.put(
            f"/api/projects/{pid}/documents/{did}",
            json={"content": "她走进咖啡馆，看到了他。两人对视一眼，又各自移开了目光。"},
        )
        return pid, did

    async def test_ai_revise_full_document(self, client):
        pid, did = await self._setup(client)
        with patch("app.services.projects.ai.get_ai_service") as mock_get_service:
            mock_service = AsyncMock()
            mock_service.revise_chapter = AsyncMock(return_value={
                "content": "她推开咖啡馆的玻璃门，一眼便看到了坐在角落的他。两人目光交错，随即默契地各自移开。",
            })
            mock_get_service.return_value = mock_service

            response = await client.post(
                f"/api/projects/{pid}/documents/{did}/ai-revise",
                json={"instruction": "增加环境描写", "ai_model": "claude-opus-4"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["document_id"] == did
        assert data["scope"] == "full"
        assert "玻璃门" in data["revised_text"]
        assert data["ai_model"] == "claude-opus-4"

    async def test_ai_revise_selection(self, client):
        pid, did = await self._setup(client)
        with patch("app.services.projects.ai.get_ai_service") as mock_get_service:
            mock_service = AsyncMock()
            mock_service.revise_chapter = AsyncMock(return_value={
                "content": "两人目光交汇的瞬间，时间仿佛静止了。",
            })
            mock_get_service.return_value = mock_service

            response = await client.post(
                f"/api/projects/{pid}/documents/{did}/ai-revise",
                json={
                    "instruction": "更有画面感",
                    "selected_text": "两人对视一眼，又各自移开了目光。",
                    "full_content": "她走进咖啡馆，看到了他。两人对视一眼，又各自移开了目光。",
                    "ai_model": "deepseek",
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["scope"] == "selection"
        assert data["original_text"] == "两人对视一眼，又各自移开了目光。"
        mock_get_service.assert_called_with("deepseek")

    async def test_ai_revise_missing_instruction(self, client):
        pid, did = await self._setup(client)
        response = await client.post(
            f"/api/projects/{pid}/documents/{did}/ai-revise",
            json={},
        )
        assert response.status_code == 422

    async def test_ai_revise_document_not_found(self, client):
        resp = await client.post("/api/projects", json=project_payload("测试"))
        pid = resp.json()["id"]
        response = await client.post(
            f"/api/projects/{pid}/documents/nonexistent/ai-revise",
            json={"instruction": "修改"},
        )
        assert response.status_code == 404

    async def test_ai_revise_debug_mode(self, client):
        pid, did = await self._setup(client)
        with patch("app.services.projects.ai.get_ai_service") as mock_get_service:
            mock_service = AsyncMock()
            mock_service.revise_chapter = AsyncMock(return_value={
                "content": "修改后的内容",
                "debug_info": {
                    "request": {"provider": "claude", "model": "claude-opus-4-6"},
                    "response": {"raw_text": "原始响应"},
                    "meta": {"truncated": False},
                },
            })
            mock_get_service.return_value = mock_service

            response = await client.post(
                f"/api/projects/{pid}/documents/{did}/ai-revise",
                json={"instruction": "测试", "debug": True},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["debug_info"] is not None
        assert data["debug_info"]["request"]["provider"] == "claude"


@pytest.mark.asyncio
class TestAIReviseMulti:
    async def _setup(self, client):
        resp = await client.post("/api/projects", json=project_payload("多模型测试"))
        pid = resp.json()["id"]
        gid = resp.json()["groups"][0]["id"]
        did = resp.json()["groups"][0]["documents"][0]["id"]
        await client.put(
            f"/api/projects/{pid}/documents/{did}",
            json={"content": "她走进咖啡馆，看到了他。"},
        )
        return pid, did

    async def test_multi_all_succeed(self, client):
        pid, did = await self._setup(client)
        with patch("app.services.projects.ai.get_ai_service") as mock_get_service:
            mock_service = AsyncMock()
            mock_service.revise_chapter = AsyncMock(return_value={"content": "修改后内容"})
            mock_get_service.return_value = mock_service

            response = await client.post(
                f"/api/projects/{pid}/documents/{did}/ai-revise-multi",
                json={"instruction": "改得更好", "models": ["claude-opus-4", "deepseek"]},
            )

        assert response.status_code == 200
        data = response.json()
        assert len(data["results"]) == 2
        assert data["scope"] == "full"
        for item in data["results"]:
            assert item["revised_text"] is not None
            assert item.get("error") is None

    async def test_multi_partial_failure(self, client):
        pid, did = await self._setup(client)
        with patch("app.services.projects.ai.get_ai_service") as mock_get_service:
            ok_service = AsyncMock()
            ok_service.revise_chapter = AsyncMock(return_value={"content": "好的内容"})
            fail_service = AsyncMock()
            fail_service.revise_chapter = AsyncMock(side_effect=Exception("API timeout"))

            def _pick(model):
                return ok_service if model == "deepseek" else fail_service
            mock_get_service.side_effect = _pick

            response = await client.post(
                f"/api/projects/{pid}/documents/{did}/ai-revise-multi",
                json={"instruction": "改", "models": ["claude-opus-4", "deepseek"]},
            )

        assert response.status_code == 200
        data = response.json()
        claude_r = next(r for r in data["results"] if r["ai_model"] == "claude-opus-4")
        ds_r = next(r for r in data["results"] if r["ai_model"] == "deepseek")
        assert claude_r["error"] is not None
        assert claude_r.get("revised_text") is None
        assert ds_r["revised_text"] == "好的内容"

    async def test_multi_with_selection(self, client):
        pid, did = await self._setup(client)
        with patch("app.services.projects.ai.get_ai_service") as mock_get_service:
            mock_service = AsyncMock()
            mock_service.revise_chapter = AsyncMock(return_value={"content": "修改片段"})
            mock_get_service.return_value = mock_service

            response = await client.post(
                f"/api/projects/{pid}/documents/{did}/ai-revise-multi",
                json={
                    "instruction": "更好",
                    "selected_text": "看到了他",
                    "models": ["deepseek"],
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["scope"] == "selection"
        assert data["original_text"] == "看到了他"
