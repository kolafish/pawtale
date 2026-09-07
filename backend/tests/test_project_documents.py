import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.content_storage import content_exists, read_content, write_content
from app.services.project_documents import (
    commit_created_document,
    commit_document_update,
    ContentRevisionConflict,
    delete_document,
    delete_project,
)


def _failing_db():
    return SimpleNamespace(
        commit=AsyncMock(side_effect=RuntimeError("commit failed")),
        rollback=AsyncMock(),
        delete=AsyncMock(),
        execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: 1)),
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


async def test_conflicting_save_never_restores_content(tmp_path):
    with patch("app.services.content_storage.PROJECTS_DIR", tmp_path / "projects"):
        await write_content("project-1", "doc-1", "committed by another writer")
        db = _failing_db()
        db.execute.return_value.scalar_one_or_none = lambda: None
        doc = SimpleNamespace(id="doc-1", word_count=0, content_revision=0)
        with patch("app.services.project_documents._snapshot_content", new_callable=AsyncMock) as snapshot:
            with pytest.raises(ContentRevisionConflict):
                await commit_document_update(db, "project-1", doc, "stale edit", expected_content_revision=0)
        snapshot.assert_not_awaited()
        assert await read_content("project-1", "doc-1") == "committed by another writer"


async def test_failed_commit_restores_before_next_writer_can_save(tmp_path):
    from app.services.project_documents import _restore_content

    restore_started = asyncio.Event()
    allow_restore = asyncio.Event()

    async def delayed_restore(*args):
        restore_started.set()
        await allow_restore.wait()
        await _restore_content(*args)

    with patch("app.services.content_storage.PROJECTS_DIR", tmp_path / "projects"):
        await write_content("project-1", "doc-1", "original")
        failed_db = _failing_db()
        next_db = _failing_db()
        next_db.commit.side_effect = None
        first = SimpleNamespace(id="doc-1", word_count=0, content_revision=0)
        second = SimpleNamespace(id="doc-1", word_count=0, content_revision=0)
        with patch("app.services.project_documents._restore_content", delayed_restore):
            failed = asyncio.create_task(commit_document_update(failed_db, "project-1", first, "failed write"))
            await asyncio.wait_for(restore_started.wait(), timeout=2)
            saved = asyncio.create_task(commit_document_update(next_db, "project-1", second, "successful write"))
            await asyncio.sleep(0)
            try:
                next_db.execute.assert_not_awaited()
            finally:
                allow_restore.set()
                results = await asyncio.gather(failed, saved, return_exceptions=True)
        assert isinstance(results[0], RuntimeError)
        assert results[1] is None
        assert await read_content("project-1", "doc-1") == "successful write"
