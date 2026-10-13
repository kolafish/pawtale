from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.domain.memoir.types import MemoirEditingJobType
from app.models.memoir import MemoirEditingJob
from app.services.memoir import automatic_book_draft, editing_jobs, story_tree_growth
from tests.memoir_helpers import ensure_mobile_memoir_project
from tests.test_memoir_book_completion import _book
from tests.test_memoir_section_organization import FakeStoryGrowthAI, _stopped_growth_job


async def test_settled_trigger_advances_same_job_without_losing_progress(client, test_db, monkeypatch):
    project_id = await ensure_mobile_memoir_project(client)
    now = datetime(2026, 10, 13, 12)
    monkeypatch.setattr(automatic_book_draft, 'utcnow_naive', lambda: now)
    first = await automatic_book_draft.schedule_book_draft(test_db, project_id=project_id)
    assert first.run_after > now
    first.result_json = {'stage': 'drafting', 'chapters': {'kept': {'status': 'ready'}}}
    await test_db.commit()
    second = await automatic_book_draft.schedule_book_draft(test_db, project_id=project_id, source='decision', settled=True)
    assert second.id == first.id and second.run_after == now
    assert second.result_json['chapters']['kept']['status'] == 'ready'
    # A later source edit still asks for settling instead of using a stale ready signal.
    incoming = await automatic_book_draft.schedule_book_draft(test_db, project_id=project_id)
    assert incoming.id == first.id and incoming.run_after > now


@pytest.mark.parametrize('dependency_type', [MemoirEditingJobType.CORRECT_TRANSCRIPT,
    MemoirEditingJobType.ORGANIZE_MATERIAL, MemoirEditingJobType.REGENERATE_CHAPTER_SECTIONS])
async def test_immediate_book_job_still_waits_for_active_dependencies(client, test_db, monkeypatch, dependency_type):
    project_id = await ensure_mobile_memoir_project(client)
    now = datetime(2026, 10, 13, 12)
    monkeypatch.setattr(automatic_book_draft, 'utcnow_naive', lambda: now)
    await editing_jobs.enqueue_editing_job(test_db, project_id=project_id, job_type=dependency_type,
        run_after=now + timedelta(minutes=1))
    parent = await automatic_book_draft.schedule_book_draft(test_db, project_id=project_id, source='retry', settled=True)
    result = await automatic_book_draft.run_book_draft_job(test_db, parent)
    assert result.status.value == 'pending' and result.result_json['stage'] == 'organizing'
    assert parent.run_after > now
    assert not await test_db.scalar(select(MemoirEditingJob.id).where(MemoirEditingJob.job_type == 'prepare_chapter'))


async def test_growth_completion_advances_the_existing_debounced_book_job(client, test_db, monkeypatch):
    project_id, _, _ = await _book(client, test_db)
    source, growth = await _stopped_growth_job(client, test_db, project_id, '小时候我住在河边。')
    fake = FakeStoryGrowthAI({'new_chapters': [{'chapter_ref': 'N1', 'title': '河边的家'}],
        'sections': [{'source_indices': [1], 'chapter_ref': 'N1', 'new_section_title': '童年的住处'}]})
    monkeypatch.setattr(story_tree_growth, 'get_ai_service', lambda _model=None: fake)
    now = datetime(2026, 10, 13, 12)
    monkeypatch.setattr(automatic_book_draft, 'utcnow_naive', lambda: now)
    parent = await automatic_book_draft.schedule_book_draft(test_db, project_id=project_id)
    assert parent.run_after > now
    await story_tree_growth.run_story_tree_growth_job(test_db, growth)
    await test_db.refresh(parent)
    assert parent.run_after == now and parent.payload_json['source'] == 'organized'


async def test_ready_signal_during_running_job_keeps_lease_and_requests_one_rerun(client, test_db, monkeypatch):
    project_id = await ensure_mobile_memoir_project(client)
    now = datetime(2026, 10, 13, 12)
    monkeypatch.setattr(automatic_book_draft, 'utcnow_naive', lambda: now)
    parent = await automatic_book_draft.schedule_book_draft(test_db, project_id=project_id)
    parent.status, parent.lease_token, parent.locked_at = 'running', 'worker-lease', now
    parent.result_json = {'stage': 'drafting'}
    await test_db.commit()
    for _ in range(2):
        resumed = await automatic_book_draft.schedule_book_draft(test_db, project_id=project_id, source='decision', settled=True)
        assert resumed.id == parent.id and resumed.status == 'running'
        assert resumed.lease_token == 'worker-lease' and resumed.rerun_requested is True
        assert resumed.result_json['stage'] == 'drafting' and resumed.run_after == now
