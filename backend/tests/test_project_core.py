from project_helpers import create_project, project_payload


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
        project = await create_project(client, "测试项目")
        response = await client.get(f"/api/projects/{project['id']}")
        assert response.status_code == 200
        data = response.json()
        assert data["type"] == "novel"
        assert data["title"] == "测试项目"

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
