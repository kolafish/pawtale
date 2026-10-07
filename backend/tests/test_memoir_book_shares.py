import secrets
from datetime import timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.auth import session_token_hash
from app.clock import utcnow_naive
from app.main import app
from app.models.memoir import (
    MemoirBookChapterDraft,
    MemoirBookChapterDraftVersion,
    MemoirBookOutput,
    MemoirBookOutputVersion,
    MemoirBookShare,
)
from app.services.memoir.book_output_snapshots import chapter_snapshot
from tests.memoir_helpers import (
    create_test_book_plan,
    create_test_chapter_draft,
    ensure_mobile_memoir_project,
)


async def _shareable_output(test_db, project_id: str, content: str):
    plan, chapter = await create_test_book_plan(test_db, project_id=project_id)
    draft = await create_test_chapter_draft(
        test_db, project_id=project_id, chapter_id=chapter.id, content=content,
    )
    draft_version = await test_db.scalar(
        select(MemoirBookChapterDraftVersion).where(MemoirBookChapterDraftVersion.draft_id == draft.id)
    )
    output = MemoirBookOutput(project_id=project_id, plan_id=plan.id, title="外婆回忆录")
    test_db.add(output)
    await test_db.flush()
    snapshot = [chapter_snapshot(
        chapter,
        draft_version,
        chapter_index=1,
        source_refs=[{"utterance_id": "private-utterance-id", "quote": "未入书的原话"}],
    )]
    version = MemoirBookOutputVersion(
        output_id=output.id,
        project_id=project_id,
        plan_id=plan.id,
        version=1,
        content=content,
        chapter_snapshot_json=snapshot,
    )
    test_db.add(version)
    await test_db.commit()
    return output, version


@pytest.mark.asyncio
async def test_book_share_is_public_read_only_version_pinned_and_revocable(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
    output, first = await _shareable_output(test_db, project_id, "第一版，只给家人阅读。")

    share_path = f"/api/mobile/memoir/projects/{project_id}/book-output/share"
    initial_status = await client.get(share_path)
    assert initial_status.headers["cache-control"] == "no-store"
    assert initial_status.json() == {
        "active": False, "expires_at": None, "token": None, "stale": False, "current": False,
    }
    created = await client.post(share_path)
    assert created.status_code == 200
    assert created.headers["cache-control"] == "no-store"
    first_token = created.json()["token"]
    assert first_token.startswith(f"v1.{output.id}.")
    recovered = await client.get(share_path)
    assert recovered.headers["cache-control"] == "no-store"
    assert recovered.json()["active"] is True
    assert recovered.json()["current"] is True
    assert recovered.json()["token"] == first_token
    assert (await client.get(share_path)).json()["token"] == first_token

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as guest:
        async def read(token):
            return await guest.post("/api/mobile/memoir/book-shares/read", json={"token": token})

        assert (await guest.get(share_path)).status_code == 401
        assert (await guest.get(f"/api/mobile/memoir/projects/{project_id}/book-output")).status_code == 401
        first_read = await read(first_token)
        assert first_read.status_code == 200
        assert first_read.headers["cache-control"] == "no-store"
        assert first_read.json()["latest_version"]["content"] == "第一版，只给家人阅读。"
        assert first_read.json()["latest_version"]["manuscript"]["chapters"][0]["sections"][0]["content"] == "第一版，只给家人阅读。"
        assert "private-utterance-id" not in first_read.text
        assert "未入书的原话" not in first_read.text
        assert len(first_read.json()["versions"]) == 1
        assert (await read(first_token[:-1] + ("0" if first_token[-1] != "0" else "1"))).status_code == 404

        second = MemoirBookOutputVersion(
            output_id=output.id,
            project_id=project_id,
            plan_id=first.plan_id,
            version=2,
            content="第二版，新增了内容。",
            chapter_snapshot_json=first.chapter_snapshot_json,
        )
        test_db.add(second)
        await test_db.commit()
        assert (await read(first_token)).status_code == 409
        assert (await client.get(share_path)).json()["current"] is False

        updated = await client.put(share_path)
        assert updated.status_code == 200
        assert updated.json()["token"] == first_token
        assert (await read(first_token)).json()["latest_version"]["content"] == second.content

        rotated = await client.post(share_path)
        assert rotated.status_code == 200
        second_token = rotated.json()["token"]
        assert second_token != first_token
        assert (await client.get(share_path)).json()["token"] == second_token
        assert (await read(first_token)).status_code == 404
        assert (await read(second_token)).json()["latest_version"]["content"] == second.content

        share = await test_db.get(MemoirBookShare, output.id)
        share.expires_at = utcnow_naive() - timedelta(seconds=1)
        await test_db.commit()
        assert (await read(second_token)).status_code == 404
        assert (await client.get(share_path)).json() == {
            "active": False, "expires_at": None, "token": None, "stale": False, "current": False,
        }

        renewed = await client.post(share_path)
        third_token = renewed.json()["token"]
        assert third_token != second_token
        assert (await read(third_token)).status_code == 200
        assert (await client.delete(share_path)).status_code == 204
        assert (await client.get(share_path)).json()["active"] is False
        assert (await read(third_token)).status_code == 404


@pytest.mark.asyncio
async def test_book_share_keeps_existing_random_tokens_readable(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
    output, version = await _shareable_output(test_db, project_id, "家人可读的第一版。")
    legacy_token = secrets.token_urlsafe(32)
    test_db.add(MemoirBookShare(
        output_id=output.id,
        version_id=version.id,
        token_hash=session_token_hash(legacy_token),
        expires_at=utcnow_naive() + timedelta(days=7),
    ))
    await test_db.commit()

    share_path = f"/api/mobile/memoir/projects/{project_id}/book-output/share"
    recovered_token = (await client.get(share_path)).json()["token"]
    assert recovered_token.startswith(f"v1.{output.id}.")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as guest:
        async def read(token):
            return await guest.post("/api/mobile/memoir/book-shares/read", json={"token": token})

        assert (await read(legacy_token)).json()["latest_version"]["content"] == version.content
        assert (await read(recovered_token)).json()["latest_version"]["content"] == version.content

        replaced = await client.post(share_path)
        assert replaced.json()["token"] != recovered_token
        assert (await read(legacy_token)).status_code == 404
        assert (await read(recovered_token)).status_code == 404


@pytest.mark.asyncio
async def test_book_share_pauses_during_unassembled_chapter_update(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
    output, first = await _shareable_output(test_db, project_id, "旧稿")
    share_path = f"/api/mobile/memoir/projects/{project_id}/book-output/share"
    token = (await client.post(share_path)).json()["token"]

    draft = await test_db.scalar(select(MemoirBookChapterDraft).where(
        MemoirBookChapterDraft.project_id == project_id,
    ))
    test_db.add(MemoirBookChapterDraftVersion(
        draft_id=draft.id,
        project_id=project_id,
        chapter_id=draft.chapter_id,
        version=2,
        content="新章节内容，尚未汇入全书稿",
    ))
    await test_db.commit()

    status = (await client.get(share_path)).json()
    assert status["active"] is True
    assert status["stale"] is True
    assert status["current"] is False
    assert (await client.post(share_path)).status_code == 409
    assert (await client.put(share_path)).status_code == 409
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as guest:
        assert (await guest.post("/api/mobile/memoir/book-shares/read", json={"token": token})).status_code == 409
    assert (await test_db.get(MemoirBookShare, output.id)).version_id == first.id
