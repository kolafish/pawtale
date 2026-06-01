from project_helpers import create_project_id, create_project_with_document


class TestReferenceAPI:
    """Reference item tests."""

    async def test_create_reference(self, client):
        project_id = await create_project_id(client)
        response = await client.post(
            f"/api/projects/{project_id}/references",
            json={"type": "character", "name": "陆辰", "content": "CEO男主"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["type"] == "character"
        assert data["name"] == "陆辰"

    async def test_list_references(self, client):
        project_id = await create_project_id(client)
        await client.post(f"/api/projects/{project_id}/references", json={"type": "note", "name": "笔记1"})
        await client.post(f"/api/projects/{project_id}/references", json={"type": "note", "name": "笔记2"})
        response = await client.get(f"/api/projects/{project_id}/references")
        assert response.status_code == 200
        assert len(response.json()) == 2

    async def test_update_reference(self, client):
        project_id = await create_project_id(client)
        create = await client.post(
            f"/api/projects/{project_id}/references",
            json={"type": "note", "name": "旧名"},
        )
        reference_id = create.json()["id"]
        response = await client.put(
            f"/api/projects/{project_id}/references/{reference_id}",
            json={"name": "新名"},
        )
        assert response.status_code == 200
        assert response.json()["name"] == "新名"

    async def test_delete_reference(self, client):
        project_id = await create_project_id(client)
        create = await client.post(
            f"/api/projects/{project_id}/references",
            json={"type": "note", "name": "删我"},
        )
        reference_id = create.json()["id"]
        response = await client.delete(f"/api/projects/{project_id}/references/{reference_id}")
        assert response.status_code == 204

    async def test_import_global_characters(self, client):
        char_response = await client.post("/api/characters", json={"name": "全局角色", "gender": "女"})
        character_id = char_response.json()["id"]
        project_id = await create_project_id(client)
        response = await client.post(
            f"/api/projects/{project_id}/references/import-global",
            json={"character_ids": [character_id], "scene_ids": []},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["type"] == "character"
        assert data[0]["name"] == "全局角色"
        assert data[0]["metadata_json"]["global_character_id"] == character_id


class TestDocumentReferenceLinks:
    """Document-reference link tests."""

    async def test_set_and_get_document_references(self, client):
        project_id, document_id = await create_project_with_document(client)
        reference = await client.post(
            f"/api/projects/{project_id}/references",
            json={"type": "note", "name": "笔记"},
        )
        reference_id = reference.json()["id"]

        response = await client.put(
            f"/api/projects/{project_id}/documents/{document_id}/references",
            json={"reference_ids": [reference_id]},
        )
        assert response.status_code == 200

        get_response = await client.get(f"/api/projects/{project_id}/documents/{document_id}/references")
        assert get_response.status_code == 200
        data = get_response.json()
        assert len(data) == 1
        assert data[0]["id"] == reference_id
