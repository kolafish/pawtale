from types import SimpleNamespace

from fastapi import HTTPException
import pytest
from sqlalchemy import select

from app.models.memoir import MemoirSection, MemoirSectionMaterial
from app.models.projects import Project
from app.services.ai_references import ModelReferenceMap
from app.services.memoir import draft_progress, story_tree_growth
from app.services.memoir.narrative import book_completion
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


@pytest.mark.parametrize("groups", [
    [],
    [{"source_indices": [1, 1], "action": "not_story"}],
    [{"source_indices": [2], "action": "not_story"}],
    [{"source_indices": [1], "action": "assign_existing", "section_id": "S99"}],
    [{"source_indices": [1], "action": "create_chapter", "new_chapter_title": "童年"}],
])
def test_invalid_model_placement_is_a_retryable_system_error_not_user_confirmation(groups):
    with pytest.raises(HTTPException) as exc:
        story_tree_growth._normalize_groups(
            story_tree_growth._GrowthOutput(groups=groups),
            utterances=[SimpleNamespace(id="source-1")],
            chapter_refs=ModelReferenceMap("C", ["chapter-1"]),
            section_refs=ModelReferenceMap("S", ["section-1"]),
        )
    assert exc.value.status_code == 422
