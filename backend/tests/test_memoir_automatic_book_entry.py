import pytest
from sqlalchemy import select

from app.models.memoir import MemoirBookPlan, MemoirEditingJob, MemoirSection, MemoirSectionMaterial
from app.models.projects import Project
from app.services.memoir import automatic_book_draft, book_plan_write_workflow, chapter_generation, draft_progress, story_tree_growth
from app.services.memoir.narrative import book_completion
from tests.memoir_helpers import create_test_voice_transcript, ensure_mobile_memoir_project
from tests.test_memoir_book_completion import _book
from tests.test_memoir_section_organization import _stopped_growth_job


@pytest.mark.parametrize("created_by", ["user", "ai"])
async def test_automatic_activation_keeps_existing_outline_and_source_ownership(client, test_db, created_by):
    project_id, chapters, _ = await _book(client, test_db, chapter_count=2)
    user_id = (await test_db.get(Project, project_id)).user_id
    for chapter in chapters:
        await book_completion.ensure_chapter_sections(test_db, project_id=project_id, chapter_id=chapter.id, user_id=user_id)
    plan = await test_db.scalar(select(MemoirBookPlan).where(MemoirBookPlan.project_id == project_id))
    plan.status, plan.created_by = "draft", created_by
    chapters[0].title = "手工调整后保留的标题"
    chapters[1].include_in_book = False
    links = (await test_db.scalars(select(MemoirSectionMaterial).where(MemoirSectionMaterial.project_id == project_id))).all()
    links[0].include_in_book = False
    before = [(item.id, item.section_id, item.utterance_id, item.include_in_book) for item in links]
    await test_db.commit()
    assert await book_plan_write_workflow.activate_existing_plan_for_automatic_book(test_db, project_id=project_id)
    await test_db.commit()
    assert not await book_plan_write_workflow.activate_existing_plan_for_automatic_book(test_db, project_id=project_id)
    assert plan.status == "confirmed" and plan.created_by == created_by
    assert chapters[0].title == "手工调整后保留的标题" and not chapters[1].include_in_book
    after = (await test_db.scalars(select(MemoirSectionMaterial).where(MemoirSectionMaterial.project_id == project_id))).all()
    assert sorted((item.id, item.section_id, item.utterance_id, item.include_in_book) for item in after) == sorted(before)


async def test_growth_into_legacy_outline_can_continue_to_writing(client, test_db, monkeypatch):
    project_id, chapters, _ = await _book(client, test_db)
    user_id = (await test_db.get(Project, project_id)).user_id
    await book_completion.ensure_chapter_sections(test_db, project_id=project_id, chapter_id=chapters[0].id, user_id=user_id)
    section = await test_db.scalar(select(MemoirSection).where(MemoirSection.chapter_id == chapters[0].id))
    plan = await test_db.scalar(select(MemoirBookPlan).where(MemoirBookPlan.project_id == project_id))
    plan.status = "draft"
    await test_db.commit()
    source, job = await _stopped_growth_job(client, test_db, project_id, "那次比赛后，老师带我去县城领奖。")
    async def decide(**kwargs):
        return [story_tree_growth.GrowthGroup(utterance_ids=[item.id for item in kwargs["utterances"]],
                                             action="assign_existing", section_id=section.id)]
    monkeypatch.setattr(story_tree_growth, "decide_story_growth", decide)
    await story_tree_growth.run_story_tree_growth_job(test_db, job)
    context = await chapter_generation.load_chapter_generation_context(test_db, project_id=project_id, chapter_id=chapters[0].id)
    assert source["utterance_id"] in {item.material_id for item in context.materials}


async def test_continue_without_previous_growth_schedules_existing_narration(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
    await create_test_voice_transcript(client, test_db, "小时候我跟奶奶去赶集。", project_id=project_id)
    await draft_progress.retry_draft_progress(test_db, project_id=project_id)
    jobs = (await test_db.scalars(select(MemoirEditingJob).where(MemoirEditingJob.project_id == project_id))).all()
    assert {job.job_type for job in jobs} >= {"organize_material", "update_book_draft"}


async def test_reconcile_legacy_outline_only_when_automatic_writing_was_requested(client, test_db):
    project_id, _, _ = await _book(client, test_db)
    plan = await test_db.scalar(select(MemoirBookPlan).where(MemoirBookPlan.project_id == project_id))
    plan.status = "draft"
    await test_db.commit()
    assert await automatic_book_draft.reconcile_books_omitted_by_editorial_review(test_db) == 0
    assert plan.status == "draft"
    parent = await automatic_book_draft.schedule_book_draft(test_db, project_id=project_id)
    parent.status, parent.error_message = "failed", "请先确认章节规划"
    await test_db.commit()
    assert await automatic_book_draft.reconcile_books_omitted_by_editorial_review(test_db) == 1
    await test_db.refresh(parent)
    assert parent.status == "pending"
    assert await automatic_book_draft.reconcile_books_omitted_by_editorial_review(test_db) == 0


pytestmark = pytest.mark.usefixtures("unchanged_story_structure")
