from sqlalchemy import select

from app.models.users import User
from project_helpers import create_project, project_payload


async def _register_admin(client, test_db) -> dict:
    await client.post("/api/auth/logout")
    response = await client.post(
        "/api/auth/register",
        json={
            "email": "admin@example.com",
            "password": "password123",
            "display_name": "管理员",
        },
    )
    assert response.status_code == 201
    admin = await test_db.scalar(select(User).where(User.email == "admin@example.com"))
    assert admin is not None
    admin.role = "admin"
    await test_db.commit()
    return response.json()["user"]


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

    async def test_project_type_templates(self, client):
        cases = {
            "novel": ("第一卷", "未命名文档"),
            "article": ("正文", "未命名文章"),
            "memoir": ("回忆片段", "第一次记录"),
        }
        for project_type, (group_name, document_name) in cases.items():
            response = await client.post("/api/projects", json=project_payload(f"{project_type} 项目", project_type))
            assert response.status_code == 201
            data = response.json()
            assert data["type"] == project_type
            assert len(data["groups"]) == 1
            assert data["groups"][0]["name"] == group_name
            assert len(data["groups"][0]["documents"]) == 1
            assert data["groups"][0]["documents"][0]["name"] == document_name
            assert data["references"] == []

    async def test_get_project(self, client):
        project = await create_project(client, "测试项目")
        response = await client.get(f"/api/projects/{project['id']}")
        assert response.status_code == 200
        data = response.json()
        assert data["type"] == "novel"
        assert data["title"] == "测试项目"

    async def test_admin_can_view_other_users_memoir_project_but_not_update_project(self, client, test_db):
        owner = (await client.get("/api/auth/me")).json()
        project = await create_project(client, "余仲尤的往事", "memoir")

        await _register_admin(client, test_db)

        list_response = await client.get("/api/projects")
        assert list_response.status_code == 200
        listed_project = next(item for item in list_response.json() if item["id"] == project["id"])
        assert listed_project["owner_user_id"] == owner["id"]
        assert listed_project["owner_email"] == owner["email"]
        assert listed_project["owner_display_name"] == owner["display_name"]
        assert listed_project["can_edit"] is False
        assert listed_project["can_edit_memoir_materials"] is True

        detail_response = await client.get(f"/api/projects/{project['id']}")
        assert detail_response.status_code == 200
        detail = detail_response.json()
        assert detail["title"] == "余仲尤的往事"
        assert detail["owner_user_id"] == owner["id"]
        assert detail["can_edit"] is False
        assert detail["can_edit_memoir_materials"] is True

        update_response = await client.put(
            f"/api/projects/{project['id']}",
            json={"title": "管理员不应改项目标题"},
        )
        assert update_response.status_code == 404

    async def test_non_admin_cannot_view_other_users_project(self, client):
        project = await create_project(client, "别人项目", "memoir")
        await client.post("/api/auth/logout")
        register = await client.post(
            "/api/auth/register",
            json={
                "email": "viewer@example.com",
                "password": "password123",
                "display_name": "普通查看者",
            },
        )
        assert register.status_code == 201

        list_response = await client.get("/api/projects")
        assert list_response.status_code == 200
        assert all(item["id"] != project["id"] for item in list_response.json())

        detail_response = await client.get(f"/api/projects/{project['id']}")
        assert detail_response.status_code == 404

    async def test_update_project(self, client):
        project = await create_project(client, "原标题")
        response = await client.put(
            f"/api/projects/{project['id']}",
            json={"title": "新标题", "word_count_target": 50000},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "新标题"
        assert data["word_count_target"] == 50000

    async def test_delete_project(self, client):
        project = await create_project(client, "将被删除")
        response = await client.delete(f"/api/projects/{project['id']}")
        assert response.status_code == 204
        get_resp = await client.get(f"/api/projects/{project['id']}")
        assert get_resp.status_code == 404

    async def test_project_not_found(self, client):
        response = await client.get("/api/projects/nonexistent")
        assert response.status_code == 404
