from datetime import timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from app.clock import utcnow_naive
from app.main import app
from app.models.memoir import MemoirBookOutput, MemoirBookOutputVersion, MemoirBookShare
from tests.memoir_helpers import create_test_book_plan, ensure_mobile_memoir_project


@pytest.mark.asyncio
async def test_book_share_is_public_read_only_version_pinned_and_revocable(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
    plan, _ = await create_test_book_plan(test_db, project_id=project_id)
    output = MemoirBookOutput(project_id=project_id, plan_id=plan.id, title="外婆回忆录")
    test_db.add(output)
    await test_db.flush()
    first = MemoirBookOutputVersion(
        output_id=output.id,
        project_id=project_id,
        plan_id=plan.id,
        version=1,
        content="第一版，只给家人阅读。",
        chapter_snapshot_json=[{
            "chapter_id": "chapter-1",
            "chapter_title": "第一章",
            "content": "第一版，只给家人阅读。",
            "source_refs": [{"utterance_id": "private-utterance-id", "quote": "未入书的原话"}],
            "sections": [{
                "section_id": "section-1",
                "title": "故事",
                "content": "第一版，只给家人阅读。",
                "used_material_ids": ["private-utterance-id"],
            }],
        }],
    )
    test_db.add(first)
    await test_db.commit()

    share_path = f"/api/mobile/memoir/projects/{project_id}/book-output/share"
    created = await client.post(share_path)
    assert created.status_code == 200
    first_token = created.json()["token"]
    assert len(first_token) == 43
    assert (await client.get(share_path)).json()["active"] is True

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as guest:
        async def read(token):
            return await guest.post("/api/mobile/memoir/book-shares/read", json={"token": token})

        assert (await guest.get(f"/api/mobile/memoir/projects/{project_id}/book-output")).status_code == 401
        first_read = await read(first_token)
        assert first_read.status_code == 200
        assert first_read.headers["cache-control"] == "no-store"
        assert first_read.json()["latest_version"]["content"] == "第一版，只给家人阅读。"
        assert first_read.json()["latest_version"]["manuscript"]["chapters"][0]["sections"][0]["content"] == "第一版，只给家人阅读。"
        assert "private-utterance-id" not in first_read.text
        assert "未入书的原话" not in first_read.text
        assert len(first_read.json()["versions"]) == 1

        second = MemoirBookOutputVersion(
            output_id=output.id,
            project_id=project_id,
            plan_id=plan.id,
            version=2,
            content="第二版，新增了内容。",
        )
        test_db.add(second)
        await test_db.commit()
        assert (await read(first_token)).json()["latest_version"]["content"] == first.content

        rotated = await client.post(share_path)
        assert rotated.status_code == 200
        second_token = rotated.json()["token"]
        assert second_token != first_token
        assert (await read(first_token)).status_code == 404
        assert (await read(second_token)).json()["latest_version"]["content"] == second.content

        share = await test_db.get(MemoirBookShare, output.id)
        share.expires_at = utcnow_naive() - timedelta(seconds=1)
        await test_db.commit()
        assert (await read(second_token)).status_code == 404
        assert (await client.get(share_path)).json()["active"] is False

        assert (await client.delete(share_path)).status_code == 204
        assert (await client.get(share_path)).json()["active"] is False
