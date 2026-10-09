import secrets
from datetime import timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.auth import SESSION_COOKIE_NAME, WECHAT_PLACEHOLDER_DISPLAY_NAME, session_token_hash
from app.clock import utcnow_naive
from app.domain.memoir.types import MemoirShareVideoJobStatus
from app.main import app
from app.models.memoir import MemoirShareLike
from app.models.users import AuthSession, User
from tests.memoir_helpers import create_test_voice_transcript, ensure_mobile_memoir_project
from tests.test_memoir_book_shares import _shareable_output
from tests.test_memoir_share_video_library import _add_video_job

LIKES = "/api/mobile/memoir/share-likes"


async def _family_member(test_db, display_name: str, avatar_url: str | None = None) -> AsyncClient:
    user = User(
        email=f"{secrets.token_hex(4)}@family.test",
        password_hash="unused",
        password_login_enabled=False,
        display_name=display_name,
        avatar_url=avatar_url,
    )
    test_db.add(user)
    await test_db.flush()
    token = secrets.token_urlsafe(32)
    test_db.add(AuthSession(
        token_hash=session_token_hash(token), user_id=user.id, expires_at=utcnow_naive() + timedelta(days=1),
    ))
    await test_db.commit()
    member = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    member.cookies.set(SESSION_COOKIE_NAME, token)
    return member


@pytest.mark.asyncio
async def test_family_likes_a_shared_book_across_link_renewal(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
    output, _ = await _shareable_output(test_db, project_id, "家里的人。")
    share_path = f"/api/mobile/memoir/projects/{project_id}/book-output/share"
    token = (await client.post(share_path)).json()["token"]
    target = {"reading_token": token}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as guest:
        visitor = await guest.post(f"{LIKES}/read", json=target)
        assert visitor.status_code == 200
        assert visitor.json() == {"count": 0, "likers": [], "liked": False, "can_like": True}
        assert (await guest.put(LIKES, json={**target, "liked": True})).status_code == 401

    aunt = await _family_member(test_db, "三姑", "/api/auth/avatars/aunt/a.jpg")
    cousin = await _family_member(test_db, WECHAT_PLACEHOLDER_DISPLAY_NAME)
    async with aunt, cousin:
        liked = await aunt.put(LIKES, json={**target, "liked": True})
        assert liked.status_code == 200, liked.text
        assert liked.json()["liked"] is True
        assert (await aunt.put(LIKES, json={**target, "liked": True})).json()["count"] == 1
        await cousin.put(LIKES, json={**target, "liked": True})

        status = (await client.get(share_path)).json()["likes"]
        assert status == {
            "count": 2,
            "likers": [
                {"display_name": "", "avatar_url": None},
                {"display_name": "三姑", "avatar_url": "/api/auth/avatars/aunt/a.jpg"},
            ],
        }
        author_view = (await client.post(f"{LIKES}/read", json=target)).json()
        assert author_view["can_like"] is False
        assert (await client.put(LIKES, json={**target, "liked": True})).status_code == 403

        # A new link for the same book keeps what family already liked.
        renewed = (await client.post(share_path)).json()["token"]
        assert (await aunt.post(f"{LIKES}/read", json={"reading_token": renewed})).json()["liked"] is True
        assert (await aunt.post(f"{LIKES}/read", json=target)).status_code == 404

        unliked = await aunt.put(LIKES, json={"reading_token": renewed, "liked": False})
        assert unliked.json()["count"] == 1
        assert unliked.json()["liked"] is False

    activity = (await client.get(f"/api/mobile/memoir/projects/{project_id}/share-likes")).json()["items"]
    assert [(item["target_kind"], item["target_title"], item["liker"]["display_name"]) for item in activity] == [
        ("book", output.title, ""),
    ]
    seen = activity[0]["created_at"]
    assert (await client.get(
        f"/api/mobile/memoir/projects/{project_id}/share-likes", params={"after": seen},
    )).json()["items"] == []


@pytest.mark.asyncio
async def test_chapter_and_video_likes_are_counted_separately(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
    created = await create_test_voice_transcript(client, test_db, project_id=project_id)
    video = await _add_video_job(
        test_db, created, job_id="00000000-0000-4000-8000-0000000000a1",
        status=MemoirShareVideoJobStatus.SUCCEEDED, created_at=utcnow_naive(), completed_at=utcnow_naive(),
    )
    await test_db.commit()
    output, version = await _shareable_output(test_db, project_id, "本章正文。")
    chapter_id = version.chapter_snapshot_json[0]["chapter_id"]
    chapter_token = (await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-output/share", params={"chapter_id": chapter_id},
    )).json()["token"]

    uncle = await _family_member(test_db, "小军")
    async with uncle:
        assert (await uncle.put(LIKES, json={"video_code": video.share_code, "liked": True})).json()["count"] == 1
        assert (await uncle.put(LIKES, json={"reading_token": chapter_token, "liked": True})).json()["count"] == 1
        assert (await uncle.post(f"{LIKES}/read", json={"video_code": "0" * 20})).status_code == 404
        assert (await uncle.post(f"{LIKES}/read", json={
            "video_code": video.share_code, "reading_token": chapter_token,
        })).status_code == 422

    library = (await client.get(f"/api/mobile/memoir/projects/{project_id}/share-video/jobs")).json()
    assert [(item["id"], item["like_count"]) for item in library["items"]] == [(video.id, 1)]
    activity = (await client.get(f"/api/mobile/memoir/projects/{project_id}/share-likes")).json()["items"]
    assert sorted(item["target_kind"] for item in activity) == ["chapter", "video"]

    # Deleting the video takes its likes with it.
    deleted = await client.request("DELETE", f"/api/mobile/memoir/share-video/jobs/{video.id}", json={"confirm": True})
    assert deleted.status_code == 204
    assert await test_db.scalar(select(func.count()).select_from(MemoirShareLike)) == 1
