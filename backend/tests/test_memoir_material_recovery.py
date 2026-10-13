from types import SimpleNamespace

from fastapi import HTTPException
import pytest
from sqlalchemy import select

from app.models.memoir import MemoirSection, MemoirSectionMaterial
from app.models.projects import Project
from app.services.ai_references import ModelReferenceMap
from app.services.memoir import draft_progress, story_tree_growth
from app.services.memoir.narrative import book_completion
from app.services.memoir.transcript_projection import source_text_hash
from tests.test_memoir_book_completion import _book
from tests.test_memoir_section_organization import _stopped_growth_job


@pytest.mark.parametrize("assign_after_context", [True, False])
async def test_deferred_source_retries_with_new_evidence_and_explicit_retry(client, test_db, monkeypatch, assign_after_context):
    project_id, chapters, _ = await _book(client, test_db)
    user_id = (await test_db.get(Project, project_id)).user_id
    await book_completion.ensure_chapter_sections(test_db, project_id=project_id, chapter_id=chapters[0].id, user_id=user_id)
    section = await test_db.scalar(select(MemoirSection).where(MemoirSection.chapter_id == chapters[0].id))
    old, job = await _stopped_growth_job(client, test_db, project_id, "他送我去参加县里的比赛，我一直记得。")
    seen = []
    async def decide(**kwargs):
        ids = [item.id for item in kwargs["utterances"]]
        seen.append(ids)
        return [story_tree_growth.GrowthGroup(
            utterance_ids=ids, action="assign_existing" if assign_after_context and len(seen) >= 3 else "leave_unassigned",
            section_id=section.id if assign_after_context and len(seen) >= 3 else None,
            reason="需要更多上下文明确人物",
        )]
    monkeypatch.setattr(story_tree_growth, "decide_story_growth", decide)
    async def run(current):
        current.status = "running"
        await test_db.commit()
        result = await story_tree_growth.run_story_tree_growth_job(test_db, current)
        current.status, current.result_json = "succeeded", result.result_json
        await test_db.commit()
        return result
    await run(job)
    await draft_progress.retry_draft_progress(test_db, project_id=project_id)
    await test_db.refresh(job)
    assert job.status == "pending"
    second = await run(job)
    assert second.result_json["growth"]["deferred"][old["utterance_id"]]["status"] == "needs_user"
    # No new evidence: a normal automatic run spends no further model call.
    job.payload_json = {**job.payload_json, "source": "transcript_ready"}
    await run(job)
    assert len(seen) == 2
    _, next_job = await _stopped_growth_job(client, test_db, project_id, "刚才说的他，就是带我参赛的张老师。")
    await run(next_job)
    assert old["utterance_id"] in seen[-1]
    link = await test_db.scalar(select(MemoirSectionMaterial).where(MemoirSectionMaterial.utterance_id == old["utterance_id"]))
    assert (link is not None) == assign_after_context
    _, _, needs_user = await draft_progress._source_counts_and_needs_user(test_db, project_id)
    assert needs_user == 0


async def test_not_story_is_not_reintroduced_by_new_context_or_retry(client, test_db, monkeypatch):
    project_id, _, _ = await _book(client, test_db)
    old, job = await _stopped_growth_job(client, test_db, project_id, "那个，我想想。")
    seen = []
    async def decide(**kwargs):
        ids = [item.id for item in kwargs["utterances"]]
        seen.append(ids)
        return [story_tree_growth.GrowthGroup(utterance_ids=ids, action="not_story", reason="没有故事")]
    monkeypatch.setattr(story_tree_growth, "decide_story_growth", decide)
    result = await story_tree_growth.run_story_tree_growth_job(test_db, job)
    job.status, job.result_json = "succeeded", result.result_json
    await test_db.commit()
    _, new_job = await _stopped_growth_job(client, test_db, project_id, "谢谢，我慢慢想。")
    new_job.payload_json = {**new_job.payload_json, "source": "retry"}
    await test_db.commit()
    await story_tree_growth.run_story_tree_growth_job(test_db, new_job)
    assert old["utterance_id"] not in seen[-1]


@pytest.mark.parametrize("payload", [
    {},
    {'placements': {'1': {'kind': 'not_story'}, '2': {'kind': 'not_story'}}},
    {'placements': {'2': {'kind': 'not_story'}}},
    {'placements': {'1': {'kind': 'story', 'section_id': 'S99'}}},
    {'new_chapters': [{'chapter_ref': 'N1', 'title': '童年'}], 'placements': {'1': {'kind': 'story', 'chapter_ref': 'N1'}}},
])
def test_invalid_model_placement_is_a_retryable_system_error_not_user_confirmation(payload):
    with pytest.raises(HTTPException) as exc:
        story_tree_growth._normalize_groups(
            story_tree_growth._GrowthOutput(**payload),
            utterances=[SimpleNamespace(id="source-1")],
            chapter_refs=ModelReferenceMap("C", ["chapter-1"]),
            section_refs=ModelReferenceMap("S", ["section-1"]),
        )
    assert exc.value.status_code == 422


@pytest.mark.parametrize("fact_blocked", [False, True])
async def test_unassigned_material_offers_recovery_without_hiding_fact_confirmation(client, test_db, fact_blocked):
    from app.services.memoir import automatic_book_draft
    project_id, chapters, _ = await _book(client, test_db)
    text = "那一年他带我去城里，我记得很清楚。"
    source, growth = await _stopped_growth_job(client, test_db, project_id, text)
    growth.status = "succeeded"
    growth.result_json = {"growth": story_tree_growth.StoryGrowthProgress(deferred={
        source["utterance_id"]: story_tree_growth.DeferredSource(
            text_hash=source_text_hash(text), status="needs_user", attempts=2, reason="人物尚不明确",
        ),
    }).model_dump(mode="json")}
    parent = await automatic_book_draft.schedule_book_draft(test_db, project_id=project_id)
    parent.status = "succeeded"
    parent.result_json = automatic_book_draft.DraftProgress(chapters={
        chapters[0].id: automatic_book_draft.ChapterProgress(status="needs_input" if fact_blocked else "ready"),
    }).model_dump(mode="json")
    await test_db.commit()
    progress = await draft_progress.read_draft_progress(test_db, project_id=project_id)
    assert progress.status == ("needs_input" if fact_blocked else "partial_ready")
    assert progress.can_retry is not fact_blocked
    assert progress.pending_material_count >= 1


async def test_archived_or_excluded_placements_are_not_unassigned_work(client, test_db):
    project_id, chapters, _ = await _book(client, test_db)
    user_id = (await test_db.get(Project, project_id)).user_id
    await book_completion.ensure_chapter_sections(test_db, project_id=project_id, chapter_id=chapters[0].id, user_id=user_id)
    section = await test_db.scalar(select(MemoirSection).where(MemoirSection.chapter_id == chapters[0].id))
    section.status, section.include_in_book = "archived", False
    await test_db.commit()
    _, pending, needs_user = await draft_progress._source_counts_and_needs_user(test_db, project_id)
    assert pending == 0 and needs_user == 0
