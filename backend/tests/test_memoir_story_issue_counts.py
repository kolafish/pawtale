import hashlib
from types import SimpleNamespace

import pytest
from sqlalchemy import update

from app.models.memoir import MemoirCompletionIssue, MemoirEditingJob
from app.models.projects import Project
from app.models.users import User
from app.services.memoir import automatic_book_draft, book_reviews
from tests.memoir_helpers import (
    create_test_book_plan_with_chapters,
    create_test_chapter_draft,
    ensure_mobile_memoir_project,
)
from tests.test_memoir_automatic_book_draft import _setup


def issue(project_id, key, *, chapter_id=None, channel="manual_review", status="open", outcome="", signature="current"):
    return MemoirCompletionIssue(
        project_id=project_id,
        chapter_id=chapter_id,
        scope="chapter" if chapter_id else "book",
        issue_type="meaning",
        channel=channel,
        question="这段内容是否保留？",
        priority="medium",
        fingerprint=hashlib.sha256(key.encode()).hexdigest(),
        input_signature=signature,
        status=status,
        outcome=outcome,
    )


async def read_snapshot(client):
    response = await client.get("/api/mobile/memoir/story-snapshot")
    assert response.status_code == 200, response.text
    return response.json()


async def test_story_counts_only_active_issues_by_channel_and_project(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
    _, chapters = await create_test_book_plan_with_chapters(test_db, project_id=project_id)
    chapter_id = chapters[0].id
    other_user = User(email="story-counts@example.com", password_hash="unused", display_name="另一位作者")
    test_db.add(other_user)
    await test_db.flush()
    other_project = Project(user_id=other_user.id, type="memoir", title="另一人的故事")
    test_db.add(other_project)
    await test_db.flush()
    test_db.add_all(
        [
            issue(project_id, "confirm", chapter_id=chapter_id),
            issue(project_id, "confirming", chapter_id=chapter_id, status="in_progress"),
            issue(project_id, "optional", chapter_id=chapter_id, channel="interview"),
            issue(project_id, "book"),
            issue(other_project.id, "foreign"),
            *[
                issue(project_id, outcome, chapter_id=chapter_id, status="closed", outcome=outcome)
                for outcome in ["confirmed", "deferred", "declined", "superseded", "answered"]
            ],
        ]
    )
    await test_db.commit()
    data = await read_snapshot(client)
    assert data["chapters"][0]["issues"] == {"review_count": 2, "supplement_count": 1}
    assert data["chapters"][0]["state_text"] == "2 项待确认"
    assert data["chapters"][1]["issues"] == {"review_count": 0, "supplement_count": 0}
    assert data["book_issues"] == {"review_count": 1, "supplement_count": 0}


async def test_one_book_decision_does_not_create_five_chapter_decisions(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
    await create_test_book_plan_with_chapters(
        test_db,
        project_id=project_id,
        chapter_titles=[f"第 {n} 章" for n in range(5)],
        draft_contents=["已有的章节正文"] * 5,
    )
    test_db.add(issue(project_id, "one-book-decision"))
    await test_db.commit()
    data = await read_snapshot(client)
    assert data["book_issues"]["review_count"] == 1
    assert all(chapter["issues"]["review_count"] == 0 for chapter in data["chapters"])
    assert all(chapter["readable"] and chapter["state"] == "settled" for chapter in data["chapters"])
    assert not data["book_readable"] and data["book_chapter_count"] == 0


@pytest.mark.parametrize("review_status", ["ready", "stale", None])
async def test_book_optional_counts_match_current_review_queue(client, test_db, monkeypatch, review_status):
    project_id = await ensure_mobile_memoir_project(client)
    test_db.add_all(
        [
            issue(project_id, "persistent-decision", signature="old"),
            issue(project_id, "current-question", channel="interview"),
            issue(project_id, "old-question", channel="interview", signature="old"),
            issue(project_id, "closed-question", channel="interview", status="closed", outcome="declined"),
        ]
    )
    await test_db.commit()

    async def current_review(_db, **kwargs):
        assert kwargs["project_id"] == project_id
        return SimpleNamespace(status=review_status, input_signature="current") if review_status else None

    monkeypatch.setattr(book_reviews, "get_current_book_review", current_review)
    data = await read_snapshot(client)
    assert data["book_issues"] == {"review_count": 1, "supplement_count": int(review_status == "ready")}


@pytest.mark.parametrize(
    "chapter_status,expected_text",
    [
        ("needs_review", "可阅读"),
        ("failed", "整理未完成"),
        ("needs_material", "待补充讲述"),
    ],
)
async def test_closed_issue_clears_even_when_generation_outcome_is_old(
    client,
    test_db,
    monkeypatch,
    chapter_status,
    expected_text,
):
    project_id, chapter_id, _, fake, parent_id = await _setup(client, test_db, monkeypatch)
    await create_test_chapter_draft(test_db, project_id=project_id, chapter_id=chapter_id, content="已有的正文")
    await test_db.execute(
        update(MemoirEditingJob).where(MemoirEditingJob.project_id == project_id).values(status="succeeded")
    )
    parent = await test_db.get(MemoirEditingJob, parent_id)
    parent.result_json = automatic_book_draft.DraftProgress(
        chapters={chapter_id: automatic_book_draft.ChapterProgress(status=chapter_status)},
        omitted_chapter_ids=[chapter_id],
        preserved_manual=True,
    ).model_dump(mode="json")
    pending = issue(project_id, "to-close", chapter_id=chapter_id)
    test_db.add(pending)
    await test_db.commit()
    assert (await read_snapshot(client))["chapters"][0]["issues"]["review_count"] == 1
    pending.status, pending.outcome = "closed", "confirmed"
    await test_db.commit()
    data = await read_snapshot(client)
    assert data["chapters"][0]["issues"] == {"review_count": 0, "supplement_count": 0}
    assert data["chapters"][0]["state_text"] == expected_text
    assert data["chapters"][0]["readable"]
    assert data["book_manual_edit_preserved"]
    assert fake.prompts == []
