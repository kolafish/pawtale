from sqlalchemy import select

from app.models.users import User
from tests.memoir_helpers import (
    create_test_book_plan_with_chapters,
    create_test_voice_transcript,
    ensure_mobile_memoir_project,
    memoir_source_ref,
)


async def _register_user(client, *, email: str, display_name: str) -> None:
    response = await client.post(
        "/api/auth/register",
        json={
            "email": email,
            "password": "password123",
            "display_name": display_name,
        },
    )
    assert response.status_code == 201


async def test_web_book_editor_admin_workflow_preserves_user_and_project_scope(client, test_db):
    owner_project_id = await ensure_mobile_memoir_project(client, "余仲尤的往事")
    first = await create_test_voice_transcript(
        client,
        test_db,
        "小时候常在老屋院子里听长辈讲故事。",
        project_id=owner_project_id,
    )
    second = await create_test_voice_transcript(
        client,
        test_db,
        "后来第一次离家去县城读书，心里很舍不得。",
        project_id=owner_project_id,
    )
    _, owner_chapters = await create_test_book_plan_with_chapters(
        test_db,
        project_id=owner_project_id,
        chapter_titles=["老屋里的童年", "第一次离家"],
        source_refs_by_chapter=[[memoir_source_ref(first)], [memoir_source_ref(second)]],
        draft_contents=["童年章节初稿。", "离家章节初稿。"],
    )

    second_project_response = await client.post(
        "/api/mobile/memoir/projects",
        json={"title": "另一本回忆录"},
    )
    assert second_project_response.status_code == 201
    second_project_id = second_project_response.json()["id"]
    other_source = await create_test_voice_transcript(
        client,
        test_db,
        "这是另一本书里的讲述。",
        project_id=second_project_id,
    )
    _, other_chapters = await create_test_book_plan_with_chapters(
        test_db,
        project_id=second_project_id,
        chapter_titles=["另一段往事"],
        source_refs_by_chapter=[[memoir_source_ref(other_source)]],
        draft_contents=["另一本书的章节稿。"],
    )

    await client.post("/api/auth/logout")
    await _register_user(client, email="book-editor-viewer@example.com", display_name="普通用户")
    denied = await client.get(f"/api/memoir/projects/{owner_project_id}/book-editor")
    assert denied.status_code == 404

    await client.post("/api/auth/logout")
    await _register_user(client, email="book-editor-admin@example.com", display_name="管理员")
    admin = await test_db.scalar(select(User).where(User.email == "book-editor-admin@example.com"))
    assert admin is not None
    admin.role = "admin"
    await test_db.commit()

    loaded = await client.get(f"/api/memoir/projects/{owner_project_id}/book-editor")
    assert loaded.status_code == 200
    loaded_data = loaded.json()
    assert [chapter["title"] for chapter in loaded_data["plan"]["chapters"]] == [
        "老屋里的童年",
        "第一次离家",
    ]
    assert len(loaded_data["chapter_drafts"]) == 2
    assert loaded_data["chapter_source_editor"]["covered_source_count"] == 2
    assert loaded_data["output"] is None

    mismatched = await client.get(
        f"/api/memoir/projects/{owner_project_id}/book-editor/chapters/{other_chapters[0].id}/draft",
    )
    assert mismatched.status_code == 404

    updated_title = await client.patch(
        f"/api/memoir/projects/{owner_project_id}/book-editor/chapters/{owner_chapters[0].id}/title",
        json={"title": "管理员校正后的童年"},
    )
    moved = await client.post(
        f"/api/memoir/projects/{owner_project_id}/book-editor/plan/structure-commands",
        json={"type": "move_chapter", "chapter_id": owner_chapters[1].id, "direction": "up"},
    )
    assert updated_title.status_code == 200
    assert updated_title.json()["chapters"][0]["title"] == "管理员校正后的童年"
    assert moved.status_code == 200
    assert [chapter["id"] for chapter in moved.json()["chapters"]] == [
        owner_chapters[1].id,
        owner_chapters[0].id,
    ]
    refreshed = await client.get(f"/api/memoir/projects/{owner_project_id}/book-editor")
    assert refreshed.status_code == 200
    assert [chapter["title"] for chapter in refreshed.json()["plan"]["chapters"]] == [
        "第一次离家",
        "管理员校正后的童年",
    ]
    assert refreshed.json()["output"] is None
