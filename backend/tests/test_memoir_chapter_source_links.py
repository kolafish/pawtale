import pytest
from sqlalchemy import select

from app.domain.memoir.types import MemoirBookPlanStatus, MemoirChapterSourceAssignedBy, MemoirChapterSourceLinkStatus
from app.models.memoir import MemoirBookPlan, MemoirBookPlanChapter, MemoirChapterSourceLink
from app.models.projects import Project
from app.services.memoir.chapter_source_links import (
    bind_source_card_to_active_utterance_links,
    mark_utterance_links_removed,
    sync_plan_chapter_source_links,
    update_utterance_link_ref_fields,
)


def _source_ref(
    utterance_id: str = "utterance-1",
    *,
    transcript_version_id: str | None = "version-1",
    audio_file_id: str | None = "audio-1",
    quote: str = "外婆家的院子",
) -> dict[str, object]:
    return {
        "voice_session_id": "voice-1",
        "message_id": f"message-{utterance_id}",
        "utterance_id": utterance_id,
        "transcript_version_id": transcript_version_id,
        "audio_file_id": audio_file_id,
        "start_ms": 120,
        "end_ms": 3450,
        "quote": quote,
    }


async def _create_plan(test_db):
    project = Project(title="回忆录项目", type="memoir")
    test_db.add(project)
    await test_db.flush()
    plan = MemoirBookPlan(
        project_id=project.id,
        title="童年",
        status=MemoirBookPlanStatus.CONFIRMED.value,
    )
    test_db.add(plan)
    await test_db.flush()
    chapter = MemoirBookPlanChapter(
        project_id=project.id,
        plan_id=plan.id,
        title="外婆家的童年",
        sort_order=0,
    )
    test_db.add(chapter)
    await test_db.flush()
    await test_db.commit()
    return project, plan, chapter


async def _links(test_db) -> list[MemoirChapterSourceLink]:
    return (
        await test_db.execute(
            select(MemoirChapterSourceLink).order_by(
                MemoirChapterSourceLink.chapter_id,
                MemoirChapterSourceLink.utterance_id,
            )
        )
    ).scalars().all()


async def test_chapter_source_links_sync_explicit_refs_is_idempotent(test_db):
    project, plan, chapter = await _create_plan(test_db)
    source_refs_by_chapter = {chapter.id: [_source_ref()]}

    first = await sync_plan_chapter_source_links(
        test_db,
        plan=plan,
        source_refs_by_chapter=source_refs_by_chapter,
        assignment_reason="replace_book_plan",
    )
    second = await sync_plan_chapter_source_links(
        test_db,
        plan=plan,
        source_refs_by_chapter=source_refs_by_chapter,
        assignment_reason="replace_book_plan",
    )
    await test_db.commit()

    links = await _links(test_db)
    assert first.created_count == 1
    assert second.created_count == 0
    assert len(links) == 1
    assert links[0].project_id == project.id
    assert links[0].chapter_id == chapter.id
    assert links[0].utterance_id == "utterance-1"
    assert links[0].transcript_version_id == "version-1"
    assert links[0].audio_file_id == "audio-1"
    assert links[0].start_ms == 120
    assert links[0].end_ms == 3450
    assert links[0].quote == "外婆家的院子"
    assert links[0].status == MemoirChapterSourceLinkStatus.ACTIVE.value
    assert links[0].assigned_by == MemoirChapterSourceAssignedBy.SYSTEM.value
    assert links[0].assignment_reason == "replace_book_plan"


async def test_chapter_source_links_sync_requires_complete_chapter_ref_map(test_db):
    _, plan, chapter = await _create_plan(test_db)
    other_chapter = MemoirBookPlanChapter(
        project_id=plan.project_id,
        plan_id=plan.id,
        title="春节厨房",
        sort_order=1,
    )
    test_db.add(other_chapter)
    await test_db.flush()
    await sync_plan_chapter_source_links(
        test_db,
        plan=plan,
        source_refs_by_chapter={
            chapter.id: [_source_ref("utterance-1")],
            other_chapter.id: [_source_ref("utterance-2")],
        },
    )
    await test_db.commit()

    with pytest.raises(ValueError):
        await sync_plan_chapter_source_links(
            test_db,
            plan=plan,
            source_refs_by_chapter={chapter.id: [_source_ref("utterance-1")]},
        )

    links = await _links(test_db)
    assert {
        (link.chapter_id, link.utterance_id, link.status)
        for link in links
    } == {
        (chapter.id, "utterance-1", MemoirChapterSourceLinkStatus.ACTIVE.value),
        (other_chapter.id, "utterance-2", MemoirChapterSourceLinkStatus.ACTIVE.value),
    }


async def test_chapter_source_links_sync_marks_missing_removed_and_reactivates(test_db):
    _, plan, chapter = await _create_plan(test_db)
    await sync_plan_chapter_source_links(test_db, plan=plan, source_refs_by_chapter={chapter.id: [_source_ref()]})
    await test_db.commit()

    removed = await sync_plan_chapter_source_links(
        test_db,
        plan=plan,
        source_refs_by_chapter={chapter.id: []},
        assignment_reason="plan_update",
    )
    await test_db.commit()
    removed_link = (await _links(test_db))[0]
    assert removed.removed_count == 1
    assert removed_link.status == MemoirChapterSourceLinkStatus.REMOVED.value
    assert removed_link.removed_at is not None

    activated = await sync_plan_chapter_source_links(
        test_db,
        plan=plan,
        source_refs_by_chapter={chapter.id: [_source_ref(quote="更新后的院子")]},
        assigned_by=MemoirChapterSourceAssignedBy.USER,
        assignment_reason="manual_readd",
    )
    await test_db.commit()
    active_link = (await _links(test_db))[0]
    assert activated.activated_count == 1
    assert active_link.status == MemoirChapterSourceLinkStatus.ACTIVE.value
    assert active_link.removed_at is None
    assert active_link.quote == "更新后的院子"
    assert active_link.assigned_by == MemoirChapterSourceAssignedBy.USER.value
    assert active_link.assignment_reason == "manual_readd"


async def test_chapter_source_links_update_and_remove_by_utterance(test_db):
    project, plan, chapter = await _create_plan(test_db)
    await sync_plan_chapter_source_links(test_db, plan=plan, source_refs_by_chapter={chapter.id: [_source_ref()]})
    await test_db.commit()
    link = (await _links(test_db))[0]
    link.source_card_id = "source-card-1"
    test_db.add(link)
    await test_db.commit()

    updated_count = await update_utterance_link_ref_fields(
        test_db,
        project_id=project.id,
        utterance_id="utterance-1",
        transcript_version_id="version-2",
        quote="外婆在院子里摘菜",
    )
    removed_count = await mark_utterance_links_removed(
        test_db,
        project_id=project.id,
        utterance_id="utterance-1",
    )
    await test_db.commit()

    link = (await _links(test_db))[0]
    assert updated_count == 1
    assert removed_count == 1
    assert link.transcript_version_id == "version-2"
    assert link.quote == "外婆在院子里摘菜"
    assert link.source_card_id is None
    assert link.status == MemoirChapterSourceLinkStatus.REMOVED.value
    assert link.removed_at is not None


async def test_chapter_source_links_bind_source_card_requires_matching_transcript_version(test_db):
    project, plan, chapter = await _create_plan(test_db)
    await sync_plan_chapter_source_links(test_db, plan=plan, source_refs_by_chapter={chapter.id: [_source_ref()]})
    await test_db.commit()

    skipped_count = await bind_source_card_to_active_utterance_links(
        test_db,
        project_id=project.id,
        utterance_id="utterance-1",
        source_card_id="source-card-1",
        transcript_version_id="version-2",
    )
    bound_count = await bind_source_card_to_active_utterance_links(
        test_db,
        project_id=project.id,
        utterance_id="utterance-1",
        source_card_id="source-card-1",
        transcript_version_id="version-1",
    )
    await test_db.commit()

    link = (await _links(test_db))[0]
    assert skipped_count == 0
    assert bound_count == 1
    assert link.source_card_id == "source-card-1"
