from project_helpers import create_project_id, create_project_with_document


class TestWritingSession:
    """Writing session tests."""

    async def test_create_and_list_sessions(self, client):
        project_id = await create_project_id(client)
        create = await client.post(
            f"/api/projects/{project_id}/sessions",
            json={"date": "2026-03-07", "words_written": 1500},
        )
        assert create.status_code == 201
        assert create.json()["words_written"] == 1500

        listing = await client.get(f"/api/projects/{project_id}/sessions")
        assert listing.status_code == 200
        assert len(listing.json()) == 1


class TestExport:
    """Export tests."""

    async def test_export_project(self, client):
        project_id, document_id = await create_project_with_document(client, "导出测试")
        await client.put(
            f"/api/projects/{project_id}/documents/{document_id}",
            json={"content": "这是测试内容，包含一些中文和English words。"},
        )
        export = await client.get(f"/api/projects/{project_id}/export")
        assert export.status_code == 200
        data = export.json()
        assert data["title"] == "导出测试"
        assert data["total_words"] > 0
        assert len(data["sections"]) == 1
