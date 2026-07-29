from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.content_storage import content_exists, read_content, write_content
from app.services.project_documents import (
    commit_created_document,
    commit_document_update,
    delete_document,
    delete_project,
)


def _failing_db():
    return SimpleNamespace(
        commit=AsyncMock(side_effect=RuntimeError("commit failed")),
        rollback=AsyncMock(),
        delete=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_commit_created_document_removes_file_when_commit_fails(tmp_path):
    with patch("app.services.content_storage.PROJECTS_DIR", tmp_path / "projects"):
        db = _failing_db()
        doc = SimpleNamespace(id="doc-1")

        with pytest.raises(RuntimeError, match="commit failed"):
            await commit_created_document(db, "project-1", doc, "draft")

        assert not content_exists("project-1", "doc-1")
        db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_commit_document_update_restores_previous_content_when_commit_fails(tmp_path):
    with patch("app.services.content_storage.PROJECTS_DIR", tmp_path / "projects"):
        await write_content("project-1", "doc-1", "old content")
        db = _failing_db()
        doc = SimpleNamespace(id="doc-1", word_count=0, content_revision=0)

        with pytest.raises(RuntimeError, match="commit failed"):
            await commit_document_update(db, "project-1", doc, "新内容 updated")

        assert await read_content("project-1", "doc-1") == "old content"
        db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_document_restores_file_when_commit_fails(tmp_path):
    with patch("app.services.content_storage.PROJECTS_DIR", tmp_path / "projects"):
        await write_content("project-1", "doc-1", "keep me")
        db = _failing_db()
        doc = SimpleNamespace(id="doc-1")

        with pytest.raises(RuntimeError, match="commit failed"):
            await delete_document(db, "project-1", doc)

        assert await read_content("project-1", "doc-1") == "keep me"
        db.delete.assert_awaited_once_with(doc)
        db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_project_restores_directory_when_commit_fails(tmp_path):
    with patch("app.services.content_storage.PROJECTS_DIR", tmp_path / "projects"):
        await write_content("project-1", "doc-1", "project content")
        db = _failing_db()
        project = SimpleNamespace(id="project-1")

        with pytest.raises(RuntimeError, match="commit failed"):
            await delete_project(db, project)

        assert await read_content("project-1", "doc-1") == "project content"
        db.delete.assert_awaited_once_with(project)
        db.rollback.assert_awaited_once()
