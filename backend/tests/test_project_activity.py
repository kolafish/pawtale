import json
from io import BytesIO
from zipfile import ZipFile

from project_helpers import create_project_id, create_project_with_document
from app.services.projects.export import markdown_to_plain_text


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

    def test_markdown_to_plain_text(self):
        text = markdown_to_plain_text(
            "# 第一章\n\n"
            "这是**加粗**和*斜体*。\n\n"
            "> 引用内容\n\n"
            "- 第一项\n"
            "- 第二项\n\n"
            "---\n\n"
            "[链接文字](https://example.com)\n\n"
            "这句~~删掉~~保留。\n\n"
            "| 人物 | 年份 |\n"
            "| --- | --- |\n"
            "| 外婆 | 1950 |\n"
        )

        assert "# " not in text
        assert "**" not in text
        assert "*斜体*" not in text
        assert "- 第一项" not in text
        assert "第一章" in text
        assert "这是加粗和斜体。" in text
        assert "引用内容" in text
        assert "第一项" in text
        assert "----------" in text
        assert "链接文字" in text
        assert "https://example.com" not in text
        assert "这句删掉保留。" in text
        assert "人物 | 年份\n外婆 | 1950" in text
        assert "---" not in text.replace("----------", "")

    async def test_download_markdown_export(self, client):
        project_id, document_id = await create_project_with_document(client, "Markdown 导出")
        await client.put(
            f"/api/projects/{project_id}/documents/{document_id}",
            json={"content": "# 正文标题\n\n这是 **Markdown**。"},
        )

        response = await client.get(f"/api/projects/{project_id}/export/download?format=markdown")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/markdown")
        assert "Markdown%20%E5%AF%BC%E5%87%BA.md" in response.headers["content-disposition"]
        assert "# Markdown 导出" in response.text
        assert "### 未命名文档" in response.text
        assert "这是 **Markdown**。" in response.text

    async def test_download_txt_export_strips_markdown(self, client):
        project_id, document_id = await create_project_with_document(client, "TXT 导出")
        await client.put(
            f"/api/projects/{project_id}/documents/{document_id}",
            json={"content": "# 正文标题\n\n这是 **Markdown**。\n\n- 列表项"},
        )

        response = await client.get(f"/api/projects/{project_id}/export/download?format=txt")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/plain")
        assert "# 正文标题" not in response.text
        assert "**Markdown**" not in response.text
        assert "- 列表项" not in response.text
        assert "正文标题" in response.text
        assert "这是 Markdown。" in response.text
        assert "列表项" in response.text

    async def test_download_zip_export_includes_manuscript_and_references(self, client):
        project_id, document_id = await create_project_with_document(client, "ZIP 导出")
        await client.put(
            f"/api/projects/{project_id}/documents/{document_id}",
            json={"content": "# 第一章\n\n这是 **Markdown** 正文。"},
        )
        reference = await client.post(
            f"/api/projects/{project_id}/references",
            json={
                "type": "outline",
                "name": "主线资料",
                "content": "大纲内容",
                "metadata_json": {"source": "manual"},
            },
        )
        assert reference.status_code == 201

        response = await client.get(f"/api/projects/{project_id}/export/download?format=zip")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/zip")
        assert "ZIP%20%E5%AF%BC%E5%87%BA.zip" in response.headers["content-disposition"]

        with ZipFile(BytesIO(response.content)) as archive:
            names = set(archive.namelist())
            root = "ZIP-导出"
            assert f"{root}/pawtale-project.json" in names
            assert f"{root}/manuscript/full.md" in names
            assert f"{root}/manuscript/full.txt" in names
            document_path = next(
                name
                for name in names
                if name.startswith(f"{root}/manuscript/")
                and name.endswith(f"{document_id}.md")
                and name != f"{root}/manuscript/full.md"
            )
            assert any(
                name.startswith(f"{root}/references/001-主线资料-") and name.endswith(".json")
                for name in names
            )

            manifest = json.loads(archive.read(f"{root}/pawtale-project.json"))
            assert manifest["title"] == "ZIP 导出"
            assert manifest["references"][0]["name"] == "主线资料"
            assert archive.read(document_path).decode() == "# 第一章\n\n这是 **Markdown** 正文。"
            assert "# 第一章" in archive.read(f"{root}/manuscript/full.md").decode()
            assert "**Markdown**" not in archive.read(f"{root}/manuscript/full.txt").decode()

            reference_path = next(name for name in names if name.startswith(f"{root}/references/001-主线资料-"))
            reference_payload = json.loads(archive.read(reference_path))
            assert reference_payload["content"] == "大纲内容"
            assert reference_payload["metadata_json"] == {"source": "manual"}
