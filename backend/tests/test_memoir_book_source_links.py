from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.domain.memoir.types import (
    MemoirBookChapterDraftStatus,
    MemoirBookPlanStatus,
    MemoirChapterSourceLinkStatus,
    MemoirCreatedBy,
)
from app.models.memoir import (
    MemoirBookChapterDraft,
    MemoirBookChapterDraftVersion,
    MemoirBookPlan,
    MemoirBookPlanChapter,
    MemoirChapterSourceLink,
)
from app.services.memoir.book_plan_sources import plan_response
from app.services.memoir.book_source_links import (
    included_chapters_by_utterance,
    plan_source_ref_dicts_by_chapter,
)
from tests.memoir_helpers import create_test_voice_transcript, ensure_mobile_memoir_project, memoir_source_ref


async def _load_plan(test_db, plan_id: str) -> MemoirBookPlan:
    return await test_db.scalar(
        select(MemoirBookPlan)
        .where(MemoirBookPlan.id == plan_id)
        .options(selectinload(MemoirBookPlan.chapters))
    )


def _link_from_created(
    *,
    project_id: str,
    plan_id: str,
    chapter_id: str,
    created: dict,
    status: MemoirChapterSourceLinkStatus = MemoirChapterSourceLinkStatus.ACTIVE,
    quote: str = "",
    merged_draft_version_id: str | None = None,
    removed_at: datetime | None = None,
) -> MemoirChapterSourceLink:
    return MemoirChapterSourceLink(
        project_id=project_id,
        plan_id=plan_id,
        chapter_id=chapter_id,
        utterance_id=created["utterance_id"],
        transcript_version_id=created["transcript_version_id"],
        audio_file_id=created["audio_file_id"],
        start_ms=0,
        end_ms=8000,
        quote=quote,
        status=status.value,
        merged_draft_version_id=merged_draft_version_id,
        removed_at=removed_at,
    )


async def test_source_link_read_uses_active_links_and_ignores_stored_json_refs(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
    first = await create_test_voice_transcript(client, test_db, "外婆家的院子很大。", project_id=project_id)
    second = await create_test_voice_transcript(client, test_db, "后来我去县城读书。", project_id=project_id)
    third = await create_test_voice_transcript(client, test_db, "母亲每天很早去工厂。", project_id=project_id)

    plan = MemoirBookPlan(
        project_id=project_id,
        title="家族回忆录",
        status=MemoirBookPlanStatus.CONFIRMED.value,
        created_by=MemoirCreatedBy.USER.value,
    )
    test_db.add(plan)
    await test_db.flush()
    first_chapter = MemoirBookPlanChapter(
        project_id=project_id,
        plan_id=plan.id,
        title="外婆家的童年",
        sort_order=0,
        source_refs_json=[
            memoir_source_ref(first, "旧的院子 quote"),
            memoir_source_ref(second, "旧的县城 quote"),
        ],
    )
    second_chapter = MemoirBookPlanChapter(
        project_id=project_id,
        plan_id=plan.id,
        title="母亲的工作",
        sort_order=1,
        source_refs_json=[memoir_source_ref(third, "母亲去工厂")],
    )
    test_db.add_all([first_chapter, second_chapter])
    await test_db.flush()
    test_db.add_all([
        _link_from_created(
            project_id=project_id,
            plan_id=plan.id,
            chapter_id=first_chapter.id,
            created=first,
            quote="link 表里的院子 quote",
        ),
        _link_from_created(
            project_id=project_id,
            plan_id=plan.id,
            chapter_id=first_chapter.id,
            created=second,
            status=MemoirChapterSourceLinkStatus.REMOVED,
            quote="已移除的县城 quote",
        ),
    ])
    await test_db.commit()

    loaded_plan = await _load_plan(test_db, plan.id)
    refs_by_chapter = await plan_source_ref_dicts_by_chapter(test_db, loaded_plan)

    assert [ref["utterance_id"] for ref in refs_by_chapter[first_chapter.id]] == [first["utterance_id"]]
    assert refs_by_chapter[first_chapter.id][0]["quote"] == "link 表里的院子 quote"
    assert refs_by_chapter[first_chapter.id][0]["voice_session_id"] == first["session"]["id"]
    assert refs_by_chapter[first_chapter.id][0]["message_id"] == first["message_id"]
    assert refs_by_chapter[second_chapter.id] == []

    included = await included_chapters_by_utterance(test_db, loaded_plan)
    assert first["utterance_id"] in included
    assert second["utterance_id"] not in included
    assert third["utterance_id"] not in included

    response = await plan_response(test_db, loaded_plan)
    first_response_refs = response.chapters[0].source_refs
    assert [ref.utterance_id for ref in first_response_refs] == [first["utterance_id"]]
    assert first_response_refs[0].quote == "link 表里的院子 quote"
    assert first_response_refs[0].created_at is not None
    assert response.chapters[1].source_refs == []


async def test_book_source_coverage_uses_source_link_read_path(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
    first = await create_test_voice_transcript(client, test_db, "小时候住在外婆家。", project_id=project_id)
    second = await create_test_voice_transcript(client, test_db, "父亲带我第一次坐火车。", project_id=project_id)

    plan = MemoirBookPlan(
        project_id=project_id,
        title="家族回忆录",
        status=MemoirBookPlanStatus.CONFIRMED.value,
        created_by=MemoirCreatedBy.USER.value,
    )
    test_db.add(plan)
    await test_db.flush()
    chapter = MemoirBookPlanChapter(
        project_id=project_id,
        plan_id=plan.id,
        title="外婆家的童年",
        sort_order=0,
        source_refs_json=[memoir_source_ref(first, "旧 JSON 仍保留")],
    )
    test_db.add(chapter)
    await test_db.flush()
    test_db.add(
        _link_from_created(
            project_id=project_id,
            plan_id=plan.id,
            chapter_id=chapter.id,
            created=second,
            quote="link 表新增素材",
        )
    )
    await test_db.commit()

    response = await client.get(f"/api/mobile/memoir/projects/{project_id}/book-source-coverage")

    assert response.status_code == 200
    data = response.json()
    included = {
        item["utterance_id"]: item["included_in_chapters"]
        for item in data["sources"]
        if item["is_included"]
    }
    assert first["utterance_id"] not in included
    assert second["utterance_id"] in included
    assert data["covered_source_count"] == 1
    assert data["uncovered_source_count"] == 1


async def test_link_only_chapter_sources_are_enough(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
    created = await create_test_voice_transcript(client, test_db, "外婆家的院子里有一棵桂花树。", project_id=project_id)

    plan = MemoirBookPlan(
        project_id=project_id,
        title="家族回忆录",
        status=MemoirBookPlanStatus.CONFIRMED.value,
        created_by=MemoirCreatedBy.USER.value,
    )
    test_db.add(plan)
    await test_db.flush()
    chapter = MemoirBookPlanChapter(
        project_id=project_id,
        plan_id=plan.id,
        title="外婆家的童年",
        sort_order=0,
        source_refs_json=[],
        metadata_json={},
    )
    test_db.add(chapter)
    await test_db.flush()
    test_db.add(
        _link_from_created(
            project_id=project_id,
            plan_id=plan.id,
            chapter_id=chapter.id,
            created=created,
            quote="桂花树",
        )
    )
    await test_db.commit()

    loaded_plan = await _load_plan(test_db, plan.id)
    response = await plan_response(test_db, loaded_plan)

    assert response.chapters[0].source_refs[0].utterance_id == created["utterance_id"]
    assert response.chapters[0].source_refs[0].quote == "桂花树"
    assert response.chapters[0].pending_source_count == 0
    assert response.chapters[0].removed_source_count == 0


async def test_link_merge_state_drives_source_change_counts(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
    first = await create_test_voice_transcript(client, test_db, "外婆家的院子很大。", project_id=project_id)
    second = await create_test_voice_transcript(client, test_db, "后来我离开县城去读书。", project_id=project_id)
    third = await create_test_voice_transcript(client, test_db, "这段后来决定不放进本章。", project_id=project_id)

    plan = MemoirBookPlan(
        project_id=project_id,
        title="家族回忆录",
        status=MemoirBookPlanStatus.CONFIRMED.value,
        created_by=MemoirCreatedBy.USER.value,
    )
    test_db.add(plan)
    await test_db.flush()
    chapter = MemoirBookPlanChapter(
        project_id=project_id,
        plan_id=plan.id,
        title="外婆家的童年",
        sort_order=0,
        source_refs_json=[],
        metadata_json={},
    )
    test_db.add(chapter)
    await test_db.flush()
    draft = MemoirBookChapterDraft(
        project_id=project_id,
        chapter_id=chapter.id,
        status=MemoirBookChapterDraftStatus.DRAFT.value,
    )
    test_db.add(draft)
    await test_db.flush()
    version = MemoirBookChapterDraftVersion(
        draft_id=draft.id,
        project_id=project_id,
        chapter_id=chapter.id,
        version=1,
        content="外婆家的院子很大。",
        created_by=MemoirCreatedBy.AI.value,
        prompt_snapshot="old prompt",
    )
    test_db.add(version)
    await test_db.flush()
    test_db.add_all([
        _link_from_created(
            project_id=project_id,
            plan_id=plan.id,
            chapter_id=chapter.id,
            created=first,
            quote="院子很大",
            merged_draft_version_id=version.id,
        ),
        _link_from_created(
            project_id=project_id,
            plan_id=plan.id,
            chapter_id=chapter.id,
            created=second,
            quote="离开县城",
        ),
        _link_from_created(
            project_id=project_id,
            plan_id=plan.id,
            chapter_id=chapter.id,
            created=third,
            status=MemoirChapterSourceLinkStatus.REMOVED,
            quote="不放进本章",
            removed_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=1),
        ),
    ])
    await test_db.commit()

    loaded_plan = await _load_plan(test_db, plan.id)
    response = await plan_response(test_db, loaded_plan)

    assert [ref.utterance_id for ref in response.chapters[0].source_refs] == [
        first["utterance_id"],
        second["utterance_id"],
    ]
    assert response.chapters[0].pending_source_count == 1
    assert response.chapters[0].removed_source_count == 1
