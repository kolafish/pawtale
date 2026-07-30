from project_helpers import create_project, create_project_id, create_project_with_group


class TestGroupAPI:
    """Document group tests."""

    async def test_create_group(self, client):
        project_id = await create_project_id(client)
        response = await client.post(f"/api/projects/{project_id}/groups", json={"name": "第二卷"})
        assert response.status_code == 201
        assert response.json()["name"] == "第二卷"

    async def test_memoir_rejects_generic_document_structure(self, client):
        project = await create_project(client, "父亲的回忆", "memoir")

        response = await client.post(
            f"/api/projects/{project['id']}/groups",
            json={"name": "回忆片段"},
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "回忆录不使用通用文档结构"

    async def test_update_group(self, client):
        project_id = await create_project_id(client)
        project = (await client.get(f"/api/projects/{project_id}")).json()
        group_id = project["groups"][0]["id"]
        response = await client.put(f"/api/projects/{project_id}/groups/{group_id}", json={"name": "序章"})
        assert response.status_code == 200
        assert response.json()["name"] == "序章"

    async def test_delete_group(self, client):
        project_id = await create_project_id(client)
        response = await client.post(f"/api/projects/{project_id}/groups", json={"name": "临时分组"})
        group_id = response.json()["id"]
        delete_response = await client.delete(f"/api/projects/{project_id}/groups/{group_id}")
        assert delete_response.status_code == 204

    async def test_reorder_groups(self, client):
        project_id = await create_project_id(client)
        project = (await client.get(f"/api/projects/{project_id}")).json()
        first_group_id = project["groups"][0]["id"]
        response = await client.post(f"/api/projects/{project_id}/groups", json={"name": "第二卷"})
        second_group_id = response.json()["id"]
        reorder_response = await client.put(
            f"/api/projects/{project_id}/groups/reorder",
            json=[
                {"id": second_group_id, "sort_order": 0},
                {"id": first_group_id, "sort_order": 1},
            ],
        )
        assert reorder_response.status_code == 200


class TestDocumentAPI:
    """Document CRUD tests."""

    async def test_create_document(self, client):
        project_id, group_id = await create_project_with_group(client)
        response = await client.post(
            f"/api/projects/{project_id}/groups/{group_id}/documents",
            json={"name": "第一章"},
        )
        assert response.status_code == 201
        assert response.json()["name"] == "第一章"

    async def test_get_document(self, client):
        project_id, _group_id = await create_project_with_group(client)
        project = (await client.get(f"/api/projects/{project_id}")).json()
        document_id = project["groups"][0]["documents"][0]["id"]
        response = await client.get(f"/api/projects/{project_id}/documents/{document_id}")
        assert response.status_code == 200
        assert "content" in response.json()

    async def test_update_document_content(self, client):
        project_id, _group_id = await create_project_with_group(client)
        project = (await client.get(f"/api/projects/{project_id}")).json()
        document_id = project["groups"][0]["documents"][0]["id"]
        response = await client.put(
            f"/api/projects/{project_id}/documents/{document_id}",
            json={"name": "序章", "content": "这是正文内容", "status": "revision"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "序章"
        assert data["content"] == "这是正文内容"
        assert data["status"] == "revision"
        assert data["word_count"] > 0
        assert data["content_revision"] == 1

    async def test_update_document_rejects_stale_content_revision(self, client):
        project_id, _group_id = await create_project_with_group(client)
        project = (await client.get(f"/api/projects/{project_id}")).json()
        document_id = project["groups"][0]["documents"][0]["id"]

        saved = await client.put(
            f"/api/projects/{project_id}/documents/{document_id}",
            json={"content": "第一端保存", "expected_content_revision": 0},
        )
        conflict = await client.put(
            f"/api/projects/{project_id}/documents/{document_id}",
            json={"content": "第二端覆盖", "expected_content_revision": 0},
        )
        current = await client.get(
            f"/api/projects/{project_id}/documents/{document_id}"
        )

        assert saved.status_code == 200
        assert saved.json()["content_revision"] == 1
        assert conflict.status_code == 409
        assert current.json()["content"] == "第一端保存"
        assert current.json()["content_revision"] == 1

    async def test_delete_document(self, client):
        project_id, group_id = await create_project_with_group(client)
        response = await client.post(
            f"/api/projects/{project_id}/groups/{group_id}/documents",
            json={"name": "临时"},
        )
        document_id = response.json()["id"]
        delete_response = await client.delete(f"/api/projects/{project_id}/documents/{document_id}")
        assert delete_response.status_code == 204

    async def test_reorder_documents(self, client):
        project_id, group_id = await create_project_with_group(client)
        project = (await client.get(f"/api/projects/{project_id}")).json()
        first_document_id = project["groups"][0]["documents"][0]["id"]
        response = await client.post(
            f"/api/projects/{project_id}/groups/{group_id}/documents",
            json={"name": "第二"},
        )
        second_document_id = response.json()["id"]
        reorder_response = await client.put(
            f"/api/projects/{project_id}/documents/reorder",
            json=[
                {"id": second_document_id, "group_id": group_id, "sort_order": 0},
                {"id": first_document_id, "group_id": group_id, "sort_order": 1},
            ],
        )
        assert reorder_response.status_code == 200
