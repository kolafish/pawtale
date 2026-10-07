import pytest
from unittest.mock import AsyncMock, patch

from project_helpers import create_project, create_project_with_document


def patch_novel_story(result: dict):
    return patch(
        "app.services.projects.ai.ai_story.generate_story",
        new=AsyncMock(return_value=result),
    )


def patch_novel_revision(
    result: dict | None = None,
    *,
    side_effect=None,
):
    return patch(
        "app.services.projects.ai.ai_revision.revise_chapter",
        new=AsyncMock(return_value=result, side_effect=side_effect),
    )


async def create_document_with_content(client, title: str, content: str) -> tuple[str, str]:
    project_id, document_id = await create_project_with_document(client, title)
    response = await client.put(
        f"/api/projects/{project_id}/documents/{document_id}",
        json={"content": content},
    )
    assert response.status_code == 200
    return project_id, document_id


class TestAIContinue:
    """AI continue endpoint tests."""

    async def test_ai_continue_success(self, client):
        project_id, document_id = await create_document_with_content(
            client,
            "AI测试",
            "窗外下着雨，她站在便利店门口。",
        )
        with (
            patch("app.services.projects.ai.get_ai_service") as mock_get_service,
            patch_novel_story({"content": "他走过来递了把伞。"}),
        ):
            mock_service = AsyncMock()
            mock_get_service.return_value = mock_service

            response = await client.post(
                f"/api/projects/{project_id}/documents/{document_id}/ai-continue",
                json={
                    "ai_model": "claude-fable-5",
                    "expected_content_revision": 1,
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["document_id"] == document_id
        assert "他走过来" in data["content"]
        assert data["ai_model"] == "claude-fable-5"
        assert data["content_revision"] == 2

    async def test_ai_continue_rejects_stale_content_revision(self, client):
        project_id, document_id = await create_document_with_content(
            client,
            "AI测试",
            "这是已经保存的正文。",
        )

        response = await client.post(
            f"/api/projects/{project_id}/documents/{document_id}/ai-continue",
            json={"expected_content_revision": 0},
        )

        assert response.status_code == 409
        current = await client.get(
            f"/api/projects/{project_id}/documents/{document_id}"
        )
        assert current.json()["content"] == "这是已经保存的正文。"
        assert current.json()["content_revision"] == 1

    async def test_ai_continue_with_instruction(self, client):
        project_id, document_id = await create_document_with_content(
            client,
            "AI测试",
            "窗外下着雨，她站在便利店门口。",
        )
        with (
            patch("app.services.projects.ai.get_ai_service") as mock_get_service,
            patch_novel_story({"content": "突然一辆车停在她面前。"}),
        ):
            mock_service = AsyncMock()
            mock_get_service.return_value = mock_service

            response = await client.post(
                f"/api/projects/{project_id}/documents/{document_id}/ai-continue",
                json={"instruction": "增加一个意外事件", "ai_model": "deepseek"},
            )

        assert response.status_code == 200
        mock_get_service.assert_called_with("deepseek")

    async def test_ai_continue_empty_document(self, client):
        project = await create_project(client, "空文档")
        document_id = project["groups"][0]["documents"][0]["id"]

        response = await client.post(
            f"/api/projects/{project['id']}/documents/{document_id}/ai-continue",
            json={},
        )
        assert response.status_code == 400

    async def test_ai_continue_document_not_found(self, client):
        project = await create_project(client)
        response = await client.post(
            f"/api/projects/{project['id']}/documents/nonexistent/ai-continue",
            json={},
        )
        assert response.status_code == 404


class TestAIRevise:
    """AI revise endpoint tests."""

    async def test_ai_revise_full_document(self, client):
        project_id, document_id = await create_document_with_content(
            client,
            "修改测试",
            "她走进咖啡馆，看到了他。两人对视一眼，又各自移开了目光。",
        )
        with (
            patch("app.services.projects.ai.get_ai_service") as mock_get_service,
            patch_novel_revision(
                {"content": "她推开咖啡馆的玻璃门，一眼便看到了坐在角落的他。两人目光交错，随即默契地各自移开。"}
            ),
        ):
            mock_service = AsyncMock()
            mock_get_service.return_value = mock_service

            response = await client.post(
                f"/api/projects/{project_id}/documents/{document_id}/ai-revise",
                json={"instruction": "增加环境描写", "ai_model": "claude-opus-4-8"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["document_id"] == document_id
        assert data["scope"] == "full"
        assert "玻璃门" in data["revised_text"]
        assert data["ai_model"] == "claude-opus-4-8"

    async def test_ai_revise_selection(self, client):
        project_id, document_id = await create_document_with_content(
            client,
            "修改测试",
            "她走进咖啡馆，看到了他。两人对视一眼，又各自移开了目光。",
        )
        with (
            patch("app.services.projects.ai.get_ai_service") as mock_get_service,
            patch_novel_revision(
                {"content": "两人目光交汇的瞬间，时间仿佛静止了。"}
            ),
        ):
            mock_service = AsyncMock()
            mock_get_service.return_value = mock_service

            response = await client.post(
                f"/api/projects/{project_id}/documents/{document_id}/ai-revise",
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
        project_id, document_id = await create_document_with_content(
            client,
            "修改测试",
            "她走进咖啡馆，看到了他。",
        )
        response = await client.post(
            f"/api/projects/{project_id}/documents/{document_id}/ai-revise",
            json={},
        )
        assert response.status_code == 422

    async def test_ai_revise_document_not_found(self, client):
        project = await create_project(client)
        response = await client.post(
            f"/api/projects/{project['id']}/documents/nonexistent/ai-revise",
            json={"instruction": "修改"},
        )
        assert response.status_code == 404

    async def test_ai_revise_debug_mode(self, client):
        project_id, document_id = await create_document_with_content(
            client,
            "修改测试",
            "她走进咖啡馆，看到了他。",
        )
        with (
            patch("app.services.projects.ai.get_ai_service") as mock_get_service,
            patch_novel_revision(
                {
                    "content": "修改后的内容",
                    "debug_info": {
                        "request": {
                            "provider": "claude",
                            "model": "claude-fable-5",
                        },
                        "response": {"raw_text": "原始响应"},
                        "meta": {"truncated": False},
                    },
                }
            ),
        ):
            mock_service = AsyncMock()
            mock_service.model_name = "claude-opus-4-8"
            mock_get_service.return_value = mock_service

            response = await client.post(
                f"/api/projects/{project_id}/documents/{document_id}/ai-revise",
                json={"instruction": "测试", "debug": True},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["debug_info"] is not None
        assert data["debug_info"]["request"]["provider"] == "claude"


@pytest.mark.asyncio
class TestAIReviseMulti:
    async def test_multi_all_succeed(self, admin_client):
        project_id, document_id = await create_document_with_content(
            admin_client,
            "多模型测试",
            "她走进咖啡馆，看到了他。",
        )
        with (
            patch("app.services.projects.ai.get_ai_service") as mock_get_service,
            patch_novel_revision({"content": "修改后内容"}),
        ):
            mock_service = AsyncMock()
            mock_get_service.return_value = mock_service

            response = await admin_client.post(
                f"/api/projects/{project_id}/documents/{document_id}/ai-revise-multi",
                json={"instruction": "改得更好", "models": ["claude-fable-5", "deepseek"]},
            )

        assert response.status_code == 200
        data = response.json()
        assert len(data["results"]) == 2
        assert data["scope"] == "full"
        for item in data["results"]:
            assert item["revised_text"] is not None
            assert item.get("error") is None

    async def test_multi_partial_failure(self, admin_client):
        project_id, document_id = await create_document_with_content(
            admin_client,
            "多模型测试",
            "她走进咖啡馆，看到了他。",
        )
        async def revise(client, **_kwargs):
            if client is fail_service:
                raise Exception("API timeout")
            return {"content": "好的内容"}

        with (
            patch("app.services.projects.ai.get_ai_service") as mock_get_service,
            patch_novel_revision(side_effect=revise),
        ):
            ok_service = AsyncMock()
            fail_service = AsyncMock()

            def pick_service(model):
                return ok_service if model == "deepseek" else fail_service

            mock_get_service.side_effect = pick_service

            response = await admin_client.post(
                f"/api/projects/{project_id}/documents/{document_id}/ai-revise-multi",
                json={"instruction": "改", "models": ["claude-fable-5", "deepseek"]},
            )

        assert response.status_code == 200
        data = response.json()
        claude_result = next(r for r in data["results"] if r["ai_model"] == "claude-fable-5")
        deepseek_result = next(r for r in data["results"] if r["ai_model"] == "deepseek")
        assert claude_result["error"] is not None
        assert claude_result.get("revised_text") is None
        assert deepseek_result["revised_text"] == "好的内容"

    async def test_multi_with_selection(self, client):
        project_id, document_id = await create_document_with_content(
            client,
            "多模型测试",
            "她走进咖啡馆，看到了他。",
        )
        with (
            patch("app.services.projects.ai.get_ai_service") as mock_get_service,
            patch_novel_revision({"content": "修改片段"}),
        ):
            mock_service = AsyncMock()
            mock_get_service.return_value = mock_service

            response = await client.post(
                f"/api/projects/{project_id}/documents/{document_id}/ai-revise-multi",
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


async def test_memoir_rejects_generic_project_ai(client):
    project = await create_project(client, "父亲的回忆", "memoir")

    analyzed = await client.post(
        f"/api/projects/{project['id']}/analyze",
        json={},
    )
    continued = await client.post(
        f"/api/projects/{project['id']}/documents/legacy-document/ai-continue",
        json={},
    )
    applied = await client.post(
        f"/api/projects/{project['id']}/analyze/apply",
        json={},
    )

    assert analyzed.status_code == 400
    assert analyzed.json()["detail"] == "仅小说项目支持项目分析"
    assert continued.status_code == 400
    assert continued.json()["detail"] == "回忆录请使用专属成书流程"
    assert applied.status_code == 400
    assert applied.json()["detail"] == "仅小说项目支持应用项目分析"
