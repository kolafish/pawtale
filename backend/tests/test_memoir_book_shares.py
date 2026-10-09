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
    MemoirChapterShare,
    MemoirBookPlanChapter,
)
from app.services.memoir.book_output_snapshots import chapter_snapshot
from app.services.memoir.book_shares import readable_share_token
from tests.memoir_helpers import (
    create_test_book_plan,
    create_test_chapter_draft,
    create_test_voice_transcript,
    memoir_source_ref,
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
        # Share videos only point at a book link a reader can open now.
        assert await readable_share_token(test_db, project_id) is None

        updated = await client.put(share_path)
        assert updated.status_code == 200
        assert updated.json()["token"] == first_token
        assert await readable_share_token(test_db, project_id) == first_token
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
        assert await readable_share_token(test_db, project_id) is None
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


@pytest.mark.asyncio
async def test_chapter_share_without_book_is_scoped_versioned_and_independently_revocable(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
    plan, chapter = await create_test_book_plan(test_db, project_id=project_id)
    draft = await create_test_chapter_draft(test_db, project_id=project_id, chapter_id=chapter.id, content="本章第一版")
    first = await test_db.scalar(select(MemoirBookChapterDraftVersion).where(MemoirBookChapterDraftVersion.draft_id == draft.id))
    other = MemoirBookPlanChapter(project_id=project_id, plan_id=plan.id, title="不分享的另一章", sort_order=1)
    test_db.add(other)
    await test_db.commit()
    share_path = f"/api/mobile/memoir/projects/{project_id}/book-output/share?chapter_id={chapter.id}"
    status = await client.get(share_path)
    assert status.status_code == 200
    assert status.json()["active"] is False
    created = await client.post(share_path)
    assert created.status_code == 200, created.text
    token = created.json()["token"]
    assert token.startswith(f"c1.{chapter.id}.")
    assert (await client.get(share_path)).json()["token"] == token
    assert await test_db.scalar(select(MemoirBookOutput).where(MemoirBookOutput.project_id == project_id)) is None

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as guest:
        async def read(link):
            return await guest.post("/api/mobile/memoir/book-shares/read", json={"token": link})

        assert (await guest.post(share_path)).status_code == 401
        response = await read(token)
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        body = response.json()
        assert body["title"] == chapter.title
        assert body["latest_version"]["manuscript"]["profile"]["title"] == chapter.title
        assert body["latest_version"]["content"] == "本章第一版"
        assert [item["chapter_id"] for item in body["latest_version"]["manuscript"]["chapters"]] == [chapter.id]
        assert [item["chapter_id"] for item in body["latest_version"]["manuscript"]["table_of_contents"]] == [chapter.id]
        assert other.id not in response.text
        assert "不分享的另一章" not in response.text
        assert (await read(token.replace("c1.", "v1.", 1))).status_code == 404
        assert (await read(token.replace(chapter.id, other.id))).status_code == 404
        missing_path = share_path.replace(chapter.id, "missing")
        assert (await client.post(missing_path)).status_code == 404

        second = MemoirBookChapterDraftVersion(
            draft_id=draft.id, project_id=project_id, chapter_id=chapter.id, version=2,
            content="本章第二版", created_by=first.created_by,
            section_snapshot_json=[{**item, "content": "本章第二版"} for item in first.section_snapshot_json],
            material_snapshot_json=first.material_snapshot_json,
            used_material_ids_json=first.used_material_ids_json,
        )
        test_db.add(second)
        await test_db.commit()
        assert (await read(token)).status_code == 409
        assert (await client.get(share_path)).json()["current"] is False
        updated = await client.put(share_path)
        assert updated.status_code == 200, updated.text
        assert updated.json() == created.json()
        assert (await read(token)).json()["latest_version"]["content"] == "本章第二版"

        # A full-book link can coexist and has an independent lifetime.
        output = MemoirBookOutput(project_id=project_id, plan_id=plan.id, title="全书")
        test_db.add(output)
        await test_db.flush()
        test_db.add(MemoirBookOutputVersion(output_id=output.id, project_id=project_id, plan_id=plan.id, version=1,
            content=second.content, chapter_snapshot_json=[chapter_snapshot(chapter, second, chapter_index=1)]))
        await test_db.commit()
        book_path = share_path.split("?")[0]
        book_token = (await client.post(book_path)).json()["token"]
        assert (await read(book_token)).status_code == 200
        rotated = (await client.post(share_path)).json()["token"]
        assert (await read(token)).status_code == 404
        assert (await read(rotated)).status_code == 200
        assert (await client.delete(share_path)).status_code == 204
        assert (await read(rotated)).status_code == 404
        assert (await read(book_token)).status_code == 200
        renewed = (await client.post(share_path)).json()["token"]
        share = await test_db.get(MemoirChapterShare, chapter.id)
        share.expires_at = utcnow_naive() - timedelta(seconds=1)
        await test_db.commit()
        assert (await read(renewed)).status_code == 404
        assert (await client.get(share_path)).json()["active"] is False


@pytest.mark.asyncio
async def test_chapter_share_pauses_when_a_saved_source_is_excluded(client, test_db):
    from app.models.memoir import MemoirSectionMaterial
    from app.services.memoir import chapter_generation

    project_id = await ensure_mobile_memoir_project(client)
    source = await create_test_voice_transcript(client, test_db, project_id=project_id)
    _, chapter = await create_test_book_plan(test_db, project_id=project_id, source_ref=memoir_source_ref(source))
    draft = await create_test_chapter_draft(test_db, project_id=project_id, chapter_id=chapter.id, content="需要更新的章节")
    context = await chapter_generation.load_chapter_generation_context(test_db, project_id=project_id, chapter_id=chapter.id)
    version = await test_db.scalar(select(MemoirBookChapterDraftVersion).where(MemoirBookChapterDraftVersion.draft_id == draft.id))
    version.input_signature = context.input_signature
    version.material_snapshot_json = chapter_generation._snapshot(context, used_material_ids={source["utterance_id"]})
    version.section_snapshot_json = [
        {**item, "input_signature": chapter_generation._section_input_signature(context, section)}
        for item, section in zip(version.section_snapshot_json, context.sections, strict=True)
    ]
    await test_db.commit()
    path = f"/api/mobile/memoir/projects/{project_id}/book-output/share?chapter_id={chapter.id}"
    token = (await client.post(path)).json()["token"]
    material = await test_db.scalar(select(MemoirSectionMaterial).where(MemoirSectionMaterial.utterance_id == source["utterance_id"]))
    material.include_in_book = False
    await test_db.commit()
    assert (await client.get(path)).json()["stale"] is True
    assert (await client.post(path)).status_code == 409
    assert (await client.put(path)).status_code == 409
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as guest:
        assert (await guest.post("/api/mobile/memoir/book-shares/read", json={"token": token})).status_code == 409
