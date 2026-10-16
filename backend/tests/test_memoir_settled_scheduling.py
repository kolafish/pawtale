from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.domain.memoir.types import MemoirEditingJobType, MemoirSessionGoal
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
    fake = FakeStoryGrowthAI({
        'new_chapters': [{'chapter_ref': 'N1', 'title': '河边的家'}],
        'placements': {'1': {'kind': 'story', 'chapter_ref': 'N1', 'new_section_title': '童年的住处'}},
    })
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


pytestmark = pytest.mark.usefixtures("unchanged_story_structure")


@pytest.mark.parametrize('session_goal', list(MemoirSessionGoal))
async def test_live_gateway_and_later_stop_extend_the_same_book_batch(client, test_db, monkeypatch, session_goal):
    from app.config import Settings, get_settings
    from app.models.memoir import MemoirVoiceSession, MemoirVoiceUtterance
    from app.models.projects import Project
    from app.services.asr.capacity import voice_gateway_capacity_manager
    from app.services.memoir import book_generation_jobs, book_review_jobs, chapter_preparation_jobs

    pid, _, book_sources = await _book(client, test_db)
    source, growth = await _stopped_growth_job(client, test_db, pid, '后来我到杭州工作。')
    session = await test_db.get(MemoirVoiceSession, source['session']['id'])
    session.session_goal = session_goal.value
    now = automatic_book_draft.utcnow_naive()
    utterance = await test_db.get(MemoirVoiceUtterance, source['utterance_id'])
    utterance.created_at = now
    session.stopped_at = now
    await test_db.commit()
    clock = [now]
    monkeypatch.setattr(get_settings(), 'memoir_book_settle_seconds', Settings().memoir_book_settle_seconds)
    monkeypatch.setattr(automatic_book_draft, 'utcnow_naive', lambda: clock[0])
    monkeypatch.setattr(story_tree_growth, 'utcnow_naive', lambda: clock[0])
    uid = await test_db.scalar(select(Project.user_id).where(Project.id == pid))
    lease = await voice_gateway_capacity_manager.acquire(uid, session.id)
    try:
        # Saving a completion answer closes the issue and dispatches this immediate trigger.
        parent = await automatic_book_draft.schedule_book_draft(test_db, project_id=pid, source='decision', settled=True)
        result = await automatic_book_draft.run_book_draft_job(test_db, parent)
        assert result.status.value == 'pending' and not result.result_json['chapters']
        if session_goal == MemoirSessionGoal.OPEN_NARRATION:
            result = await story_tree_growth.run_story_tree_growth_job(test_db, growth)
            assert result.status.value == 'pending' and result.result_json['stage'] == 'waiting_for_batch'
        # Previously queued children also wait, including an explicit retry during a live call.
        for handler, payload in [
            (chapter_preparation_jobs.run_chapter_preparation_job, {'automatic': True}),
            (book_generation_jobs.run_book_editorial_copy_generation_job, {'automatic': True}),
            (book_review_jobs.run_book_review_job, {'apply_structure_guidance': True, 'retry': True}),
        ]:
            child = MemoirEditingJob(project_id=pid, payload_json=payload,
                first_started_at=now - timedelta(hours=2), result_json={'kept': 'checkpoint'})
            result = await handler(test_db, child)
            assert result.status.value == 'pending' and result.result_json['kept'] == 'checkpoint'
            assert child.first_started_at is None
    finally:
        await lease.release()
    # Persisted listening state is resumable, not proof of a live socket.
    session.status = 'listening'
    session.stopped_at = None
    utterance = await test_db.get(MemoirVoiceUtterance, source['utterance_id'])
    utterance.created_at = now
    await test_db.commit()
    assert await automatic_book_draft.narration_ready_at(test_db, project_id=pid) == now + timedelta(seconds=30)
    assert await automatic_book_draft.narration_ready_at(test_db, project_id=pid, settle=False) == now
    clock[0] += timedelta(seconds=10)
    utterance.created_at = clock[0]
    await test_db.commit()
    assert await automatic_book_draft.narration_ready_at(test_db, project_id=pid) == now + timedelta(seconds=40)
    clock[0] += timedelta(seconds=10)
    session.stopped_at = clock[0]
    session.status = 'stopped'
    await test_db.commit()
    assert await automatic_book_draft.narration_ready_at(test_db, project_id=pid) == now + timedelta(seconds=23)
    clock[0] += timedelta(seconds=3)
    assert await automatic_book_draft.narration_ready_at(test_db, project_id=pid) == clock[0]
    later = await test_db.get(MemoirVoiceUtterance, book_sources[0]['utterance_id'])
    later.created_at = clock[0]
    await test_db.commit()
    assert await automatic_book_draft.narration_ready_at(test_db, project_id=pid) == clock[0] + timedelta(seconds=30)


async def _add_narration(test_db, session, text, *, role='user'):
    from app.models.memoir import MemoirChatMessage, MemoirVoiceUtterance

    message = MemoirChatMessage(project_id=session.project_id, voice_session_id=session.id,
        role=role, content_text=text, content_status='corrected')
    test_db.add(message)
    await test_db.flush()
    utterance = MemoirVoiceUtterance(voice_session_id=session.id, chat_message=message,
        transcript_raw=text, transcript_edited=text)
    test_db.add(utterance)
    await test_db.commit()
    return utterance


async def test_disconnect_buffer_starts_at_disconnect_even_after_long_silence(client, test_db, monkeypatch):
    from app.config import get_settings
    from app.models.memoir import MemoirVoiceSession, MemoirVoiceUtterance
    from app.services.memoir import voice_sessions

    pid = await ensure_mobile_memoir_project(client)
    source, growth = await _stopped_growth_job(client, test_db, pid, '小时候我们常在院子里玩。')
    session = await test_db.get(MemoirVoiceSession, source['session']['id'])
    session.status, session.stopped_at = 'listening', None
    now = automatic_book_draft.utcnow_naive()
    utterance = await test_db.get(MemoirVoiceUtterance, source['utterance_id'])
    utterance.created_at = now - timedelta(minutes=10)
    growth.status, growth.run_after = 'pending', now + timedelta(minutes=10)
    await test_db.commit()
    monkeypatch.setattr(get_settings(), 'memoir_book_settle_seconds', 30)
    monkeypatch.setattr(automatic_book_draft, 'utcnow_naive', lambda: now)
    monkeypatch.setattr(story_tree_growth, 'utcnow_naive', lambda: now)
    monkeypatch.setattr(voice_sessions, 'utcnow_naive', lambda: now)
    await voice_sessions.mark_voice_session_disconnected(test_db, session.id, source['user_id'])
    assert session.status == 'listening' and session.stopped_at is None
    assert await automatic_book_draft.narration_ready_at(test_db, project_id=pid) == now + timedelta(seconds=30)
    assert growth.run_after == now + timedelta(seconds=30)
    # A later clean stop supersedes this recovery buffer, without rewriting history.
    session.status, session.stopped_at = 'stopped', now
    await test_db.commit()
    assert await automatic_book_draft.narration_ready_at(test_db, project_id=pid) == now + timedelta(seconds=3)


@pytest.mark.parametrize("stop_during_batch", [False, True])
async def test_live_five_material_batch_freezes_sources_and_leaves_book_work_waiting(client, test_db, monkeypatch, stop_during_batch):
    from app.models.memoir import MemoirSectionMaterial, MemoirVoiceSession
    from app.services.asr.capacity import voice_gateway_capacity_manager
    from app.services.memoir import book_generation_jobs, book_review_jobs, chapter_preparation_jobs

    pid, chapters, _ = await _book(client, test_db)
    source, growth = await _stopped_growth_job(client, test_db, pid, '小时候我在成都上学。')
    session = await test_db.get(MemoirVoiceSession, source['session']['id'])
    session.status, session.stopped_at = 'listening', None
    ready = [source['utterance_id']]
    for text in ['放学后我去河边玩。', '同学常来我家。', '老师教我们写字。']:
        ready.append((await _add_narration(test_db, session, text)).id)
    await _add_narration(test_db, session, '嗯。')
    await _add_narration(test_db, session, '后来怎样了？', role='assistant')
    correcting = await _add_narration(test_db, session, '后来我搬到了杭州。')
    await editing_jobs.enqueue_editing_job(test_db, project_id=pid,
        job_type=MemoirEditingJobType.CORRECT_TRANSCRIPT,
        payload_json={'utterance_id': correcting.id}, dedupe_key=f'correct_transcript:{correcting.id}')
    await test_db.commit()
    digests, late = [], []

    async def decide(**kwargs):
        assert {item.id for item in kwargs['utterances']} == set(ready)
        late.append((await _add_narration(test_db, session, '这是模型开始整理后才讲的一段。')).id)
        if stop_during_batch:
            from app.schemas.memoir_voice import MemoirVoiceSessionStopRequest
            from app.services.memoir import voice_sessions
            await voice_sessions.stop_voice_session(test_db, session.id, source['user_id'], MemoirVoiceSessionStopRequest())
        return [story_tree_growth.GrowthGroup(action='create_section', chapter_id=chapters[0].id,
            new_section_title='童年的学校', utterance_ids=list(ready))]

    async def digest(_db, **kwargs):
        digests.append(kwargs['chapter_id'])

    async def no_directory_review(*_args, **_kwargs):
        pytest.fail('live batches must not review the whole directory')

    monkeypatch.setattr(story_tree_growth, 'decide_story_growth', decide)
    monkeypatch.setattr(story_tree_growth, 'generate_or_reuse_chapter_digest', digest)
    monkeypatch.setattr(story_tree_growth, 'review_story_structure', no_directory_review)
    lease = await voice_gateway_capacity_manager.acquire(source['user_id'], session.id)
    try:
        result = await story_tree_growth.run_story_tree_growth_job(test_db, growth)
        assert result.status.value == 'pending' and result.result_json['stage'] == 'waiting_for_batch'
        assert not digests
        ready.append((await _add_narration(test_db, session, '每到夏天我们一起游泳。')).id)
        late.append((await _add_narration(test_db, session, '这是已积压但排在第六条的讲述。')).id)
        growth.status = 'pending'
        await test_db.commit()
        queued = await story_tree_growth.schedule_story_tree_growth(test_db, project_id=pid,
            voice_session_id=session.id, source='transcript_corrected')
        assert queued.id == growth.id and queued.run_after <= story_tree_growth.utcnow_naive()
        growth.status = 'running'
        await test_db.commit()
        result = await story_tree_growth.run_story_tree_growth_job(test_db, growth)
        assert result.status.value == 'pending'
        placed = set((await test_db.scalars(select(MemoirSectionMaterial.utterance_id).where(
            MemoirSectionMaterial.utterance_id.in_([*ready, correcting.id, *late]),
        ))).all())
        assert placed == set(ready) and digests == [chapters[0].id]
        # Same inputs do not incur another organization/digest pass.
        result = await story_tree_growth.run_story_tree_growth_job(test_db, growth)
        assert result.status.value == 'pending' and digests == [chapters[0].id]
        parent = await automatic_book_draft.schedule_book_draft(test_db, project_id=pid, settled=True)
        result = await automatic_book_draft.run_book_draft_job(test_db, parent)
        assert result.status.value == 'pending' and not result.result_json['chapters']
        for handler, payload in [
            (chapter_preparation_jobs.run_chapter_preparation_job, {'automatic': True}),
            (book_generation_jobs.run_book_editorial_copy_generation_job, {'automatic': True}),
            (book_review_jobs.run_book_review_job, {'apply_structure_guidance': True}),
        ]:
            child = MemoirEditingJob(project_id=pid, payload_json=payload, result_json={})
            assert (await handler(test_db, child)).status.value == 'pending'
    finally:
        await lease.release()

    # Flush the short tail and perform one final directory check after Stop.
    from app.schemas.memoir_voice import MemoirVoiceSessionStopRequest
    from app.services.memoir import voice_sessions

    correction = await test_db.scalar(select(MemoirEditingJob).where(
        MemoirEditingJob.dedupe_key == f'correct_transcript:{correcting.id}'))
    correction.status = 'succeeded'
    growth.status = 'pending'
    await test_db.commit()
    await voice_sessions.stop_voice_session(test_db, session.id, source['user_id'], MemoirVoiceSessionStopRequest())
    reviews = []

    async def final_review(*_args, **_kwargs):
        reviews.append(True)
        return False

    async def tail(**kwargs):
        assert {item.id for item in kwargs['utterances']} == {correcting.id, *late}
        return [story_tree_growth.GrowthGroup(action='create_section', chapter_id=chapters[0].id,
            new_section_title='后来的故事', utterance_ids=[correcting.id, *late])]

    monkeypatch.setattr(story_tree_growth, 'review_story_structure', final_review)
    monkeypatch.setattr(story_tree_growth, 'decide_story_growth', tail)
    growth.status = 'running'
    await test_db.commit()
    assert (await story_tree_growth.run_story_tree_growth_job(test_db, growth)).status.value == 'succeeded'
    assert reviews == [True]


async def test_first_live_batch_plans_only_the_frozen_corrected_sources(client, test_db, monkeypatch):
    from app.models.memoir import MemoirSectionMaterial, MemoirVoiceSession
    from app.services.asr.capacity import voice_gateway_capacity_manager
    from app.services.memoir import book_plan_write_workflow as plans
    from app.services.memoir.book_plan_ai import GeneratedBookPlan, chapters_from_payload

    pid = await ensure_mobile_memoir_project(client)
    source, growth = await _stopped_growth_job(client, test_db, pid, '小时候我们在院子里玩。')
    session = await test_db.get(MemoirVoiceSession, source['session']['id'])
    session.status, session.stopped_at = 'listening', None
    ready = {source['utterance_id']}
    for index in range(4):
        ready.add((await _add_narration(test_db, session, f'这是童年的第{index + 1}段往事。')).id)
    pending = await _add_narration(test_db, session, '这段原始转写还在校正。')
    await editing_jobs.enqueue_editing_job(test_db, project_id=pid, job_type=MemoirEditingJobType.CORRECT_TRANSCRIPT,
        dedupe_key=f'correct_transcript:{pending.id}', payload_json={'utterance_id': pending.id})
    await test_db.commit()
    calls = []

    async def plan(**kwargs):
        calls.append({item.utterance_id for item in kwargs['sources']})
        title, chapters = chapters_from_payload({'title': '童年', 'chapters': [
            {'title': '院子里的往事', 'source_indices': list(range(1, 6))},
        ]}, sources=kwargs['sources'])
        return GeneratedBookPlan(title=title, chapters=chapters, model='fixture', prompt='fixture')

    async def no_extra_structure(*_args, **_kwargs):
        return None

    monkeypatch.setattr(plans, '_generate_book_plan_from_sources', plan)
    monkeypatch.setattr(story_tree_growth, 'regenerate_chapter_sections', no_extra_structure)
    monkeypatch.setattr(story_tree_growth, 'generate_or_reuse_chapter_digest', no_extra_structure)
    lease = await voice_gateway_capacity_manager.acquire(source['user_id'], session.id)
    try:
        assert (await story_tree_growth.run_story_tree_growth_job(test_db, growth)).status.value == 'pending'
        assert calls == [ready]
        assert set((await test_db.scalars(select(MemoirSectionMaterial.utterance_id).where(
            MemoirSectionMaterial.project_id == pid))).all()) == ready
    finally:
        await lease.release()


async def test_stop_flushes_less_than_five_materials_without_live_batch_threshold(client, test_db, monkeypatch):
    from app.config import get_settings
    from app.models.memoir import MemoirVoiceSession

    pid, _, _ = await _book(client, test_db)
    source, growth = await _stopped_growth_job(client, test_db, pid, '小时候我住在河边。')
    session = await test_db.get(MemoirVoiceSession, source['session']['id'])
    now = automatic_book_draft.utcnow_naive()
    session.stopped_at = now
    await test_db.commit()
    fake = FakeStoryGrowthAI({'new_chapters': [{'chapter_ref': 'N1', 'title': '河边的家'}],
        'placements': {'1': {'kind': 'story', 'chapter_ref': 'N1', 'new_section_title': '童年的住处'}}})
    monkeypatch.setattr(story_tree_growth, 'get_ai_service', lambda _model=None: fake)
    monkeypatch.setattr(get_settings(), 'memoir_book_settle_seconds', 30)
    clock = [now]
    monkeypatch.setattr(automatic_book_draft, 'utcnow_naive', lambda: clock[0])
    monkeypatch.setattr(story_tree_growth, 'utcnow_naive', lambda: clock[0])
    result = await story_tree_growth.run_story_tree_growth_job(test_db, growth)
    assert result.status.value == 'pending' and fake.calls == 0
    clock[0] += timedelta(seconds=3)
    assert (await story_tree_growth.run_story_tree_growth_job(test_db, growth)).status.value == 'succeeded'
    assert fake.calls == 1


async def test_concurrent_stops_wake_pending_work_without_locking_each_others_jobs(client, test_db, monkeypatch):
    import asyncio
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.models.memoir import MemoirVoiceSession
    from app.schemas.memoir_voice import MemoirVoiceSessionStopRequest
    from app.services.memoir import voice_sessions

    pid = await ensure_mobile_memoir_project(client)
    records = [await _stopped_growth_job(client, test_db, pid, text) for text in ['成都的童年。', '杭州的工作。']]
    for source, job in records:
        session = await test_db.get(MemoirVoiceSession, source['session']['id'])
        session.status = 'listening'
        job.status, job.run_after = 'pending', automatic_book_draft.utcnow_naive() + timedelta(minutes=10)
    parent = await automatic_book_draft.schedule_book_draft(test_db, project_id=pid)
    await test_db.commit()
    entered, count = asyncio.Event(), 0
    original = story_tree_growth.schedule_story_tree_growth

    async def overlap(db, **kwargs):
        nonlocal count
        result = await original(db, **kwargs)
        count += 1
        if count == 2:
            entered.set()
        await asyncio.wait_for(entered.wait(), timeout=3)
        return result

    monkeypatch.setattr(story_tree_growth, 'schedule_story_tree_growth', overlap)
    async with AsyncSession(test_db.bind, expire_on_commit=False) as first, AsyncSession(test_db.bind, expire_on_commit=False) as second:
        results = await asyncio.wait_for(asyncio.gather(*[
            voice_sessions.stop_voice_session(db, source['session']['id'], source['user_id'], MemoirVoiceSessionStopRequest())
            for db, (source, _) in zip([first, second], records, strict=True)
        ]), timeout=5)
    assert all(result.session.status.value == 'stopped' for result in results)
    await test_db.refresh(parent)
    assert parent.run_after <= automatic_book_draft.utcnow_naive() + timedelta(seconds=3)


async def test_live_batch_combines_sessions_and_only_one_existing_job_organizes_it(client, test_db, monkeypatch):
    from app.models.memoir import MemoirVoiceSession
    from app.services.asr.capacity import voice_gateway_capacity_manager

    pid, _, _ = await _book(client, test_db)
    source, first = await _stopped_growth_job(client, test_db, pid, '童年在成都生活。')
    old = await test_db.get(MemoirVoiceSession, source['session']['id'])
    ready = {source['utterance_id'], (await _add_narration(test_db, old, '放学后在院子里玩。')).id}
    current = MemoirVoiceSession(project_id=pid, status='listening')
    test_db.add(current)
    await test_db.flush()
    for index in range(3):
        ready.add((await _add_narration(test_db, current, f'后来在杭州的第{index + 1}件往事。')).id)
    second = MemoirEditingJob(project_id=pid, job_type='organize_material', status='running',
        payload_json={'voice_session_id': current.id}, run_after=automatic_book_draft.utcnow_naive(),
        created_at=first.created_at + timedelta(seconds=1))
    test_db.add(second)
    await test_db.commit()
    calls = []

    async def grow(_db, **kwargs):
        calls.append(kwargs['source_ids'])
        return editing_jobs.EditingJobResult(), False

    monkeypatch.setattr(story_tree_growth, 'grow_story_tree', grow)
    lease = await voice_gateway_capacity_manager.acquire(source['user_id'], current.id)
    try:
        assert (await story_tree_growth.run_story_tree_growth_job(test_db, second)).status.value == 'pending'
        assert calls == []
        assert (await story_tree_growth.run_story_tree_growth_job(test_db, first)).status.value == 'pending'
        assert calls == [ready]
        assert (await story_tree_growth.run_story_tree_growth_job(test_db, second)).status.value == 'pending'
        assert calls == [ready]
    finally:
        await lease.release()
