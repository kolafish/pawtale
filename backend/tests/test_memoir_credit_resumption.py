import asyncio
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.application_errors import InsufficientAICreditError
from app.domain.memoir.types import MemoirEditingJobType
from app.models.memoir import MemoirEditingJob
from app.models.projects import Project
from app.services.ai_credits import add_ai_credit, ensure_ai_credit_account
from app.services.memoir import automatic_book_draft, editing_jobs, voice_transcripts
from tests.memoir_helpers import ensure_mobile_memoir_project, run_test_editing_job
from tests.test_memoir_automatic_book_draft import _setup


async def _wallet(db, user_id, balance):
    account = await ensure_ai_credit_account(db, user_id)
    account.free_credit_rmb = Decimal(balance)
    account.recharged_credit_rmb = account.manual_grant_rmb = Decimal(0)
    await db.commit()
    return account


async def _blocked(db, project_id, *, status='failed', error='InsufficientAICreditError', cancelled=False):
    job = await editing_jobs.enqueue_editing_job(db, project_id=project_id,
        job_type=MemoirEditingJobType.ORGANIZE_MATERIAL, payload_json={'voice_session_id': 'unchanged'}, dedupe_key=str(uuid4()))
    job.status = status
    job.result_json = {'growth': {'placed': ['saved-source']}, 'provider_request_pending': True,
        'failure': {'error_type': error}, 'cancelled': cancelled}
    job.error_message = 'AI 额度已用尽'
    await db.commit()
    return job


async def test_recovery_covers_all_account_books_once_and_preserves_checkpoints(client, test_db):
    first_id = await ensure_mobile_memoir_project(client)
    project = await test_db.get(Project, first_id)
    second = Project(title='另一本回忆录', type='memoir', user_id=project.user_id)
    unrelated = Project(title='没有额度的其他账号', type='memoir')
    test_db.add_all([second, unrelated])
    await test_db.commit()
    await _wallet(test_db, project.user_id, '0')
    jobs = [await _blocked(test_db, p) for p in [first_id, second.id]]
    other = await _blocked(test_db, unrelated.id)
    superseded = await _blocked(test_db, first_id)
    superseded.dedupe_key = None
    manual = await _blocked(test_db, first_id)
    manual.job_type = 'prepare_chapter'
    manual.payload_json = {'automatic': False}
    await test_db.commit()
    timeout = await _blocked(test_db, first_id, error='AIProviderTimeoutError')
    cancelled = await _blocked(test_db, first_id, cancelled=True)
    skipped = await _blocked(test_db, first_id, status='skipped')
    running = await _blocked(test_db, first_id, status='running')
    parent = await automatic_book_draft.schedule_book_draft(test_db, project_id=first_id)
    parent.status, parent.result_json = 'succeeded', {'stage': 'needs_attention'}
    await test_db.commit()
    assert await editing_jobs.resume_credit_blocked_editing_jobs(test_db) == 0
    await add_ai_credit(test_db, user_id=project.user_id, amount_rmb=Decimal('5'), kind='grant', actor_user_id=project.user_id)
    assert await editing_jobs.resume_credit_blocked_editing_jobs(test_db) == 2
    assert await editing_jobs.resume_credit_blocked_editing_jobs(test_db) == 0
    for job in jobs:
        await test_db.refresh(job)
        assert job.status == 'pending' and job.payload_json == {'voice_session_id': 'unchanged'}
        assert job.result_json['growth']['placed'] == ['saved-source']
        assert job.result_json['provider_request_pending'] is False
        assert job.first_started_at is None
    await test_db.refresh(parent)
    assert parent.status == 'pending' and parent.result_json['stage'] == 'needs_attention'
    for job, status in [(other, 'failed'), (superseded, 'failed'), (manual, 'failed'), (timeout, 'failed'), (cancelled, 'failed'), (skipped, 'skipped'), (running, 'running')]:
        await test_db.refresh(job)
        assert job.status == status


async def test_credit_arriving_before_failure_is_persisted_is_recovered(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
    user_id = (await test_db.get(Project, project_id)).user_id
    await _wallet(test_db, user_id, '0')
    job = await _blocked(test_db, project_id, status='running')
    await add_ai_credit(test_db, user_id=user_id, amount_rmb=Decimal('5'), kind='recharge', actor_user_id=user_id)
    assert await editing_jobs.resume_credit_blocked_editing_jobs(test_db) == 0
    job.status = 'failed'
    await test_db.commit()
    assert await editing_jobs.resume_credit_blocked_editing_jobs(test_db) == 1


async def test_unrelated_failed_parent_is_not_restarted(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
    user_id = (await test_db.get(Project, project_id)).user_id
    await _wallet(test_db, user_id, '5')
    await _blocked(test_db, project_id)
    parent = await automatic_book_draft.schedule_book_draft(test_db, project_id=project_id)
    parent.status, parent.result_json = 'failed', {'failure': {'error_type': 'AIProviderTimeoutError'}}
    await test_db.commit()
    assert await editing_jobs.resume_credit_blocked_editing_jobs(test_db) == 1
    await test_db.refresh(parent)
    assert parent.status == 'failed'


@pytest.mark.parametrize('strict', [True, False])
async def test_credit_failure_never_becomes_a_successful_local_transcript_fallback(monkeypatch, strict):
    async def insufficient(*args, **kwargs):
        raise InsufficientAICreditError('AI 额度已用尽')
    monkeypatch.setattr(voice_transcripts, 'generate_text_with_client', insufficient)
    with pytest.raises(InsufficientAICreditError):
        await voice_transcripts.generate_cleaned_transcript_text('我小时候住在河边，每天和哥哥走路去学校。', ai_client=object(), strict=strict)


async def test_stopped_chapter_resumes_through_the_real_book_pipeline(client, test_db, monkeypatch):
    project_id, chapter_id, _, _, parent_id = await _setup(client, test_db, monkeypatch)
    user_id = (await test_db.get(Project, project_id)).user_id
    await _wallet(test_db, user_id, '0')
    # The existing fake chapter provider still passes through the real shared credit guard.
    assert not await run_test_editing_job(test_db, parent_id)
    parent = await test_db.get(MemoirEditingJob, parent_id)
    child_id = parent.result_json['chapters'][chapter_id]['job_id']
    assert not await run_test_editing_job(test_db, child_id)
    child = await test_db.get(MemoirEditingJob, child_id)
    assert child.status == 'failed'
    assert child.result_json['failure']['error_type'] == 'InsufficientAICreditError'
    await run_test_editing_job(test_db, parent_id)
    assert await editing_jobs.resume_credit_blocked_editing_jobs(test_db) == 0
    await add_ai_credit(test_db, user_id=user_id, amount_rmb=Decimal('5'), kind='recharge', actor_user_id=user_id)
    assert await editing_jobs.resume_credit_blocked_editing_jobs(test_db) == 1
    async def unavailable_copy(**kwargs):
        return None
    monkeypatch.setattr('app.services.memoir.book_outputs.generate_manuscript_editorial_copy', unavailable_copy)
    assert await run_test_editing_job(test_db, child_id)
    assert not await run_test_editing_job(test_db, parent_id)
    await test_db.refresh(parent)
    assert await run_test_editing_job(test_db, parent.result_json['review_job_id'])
    assert await run_test_editing_job(test_db, parent_id)
    assert parent.result_json['stage'] == 'ready'
    assert len((await test_db.scalars(select(MemoirEditingJob.id).where(
        MemoirEditingJob.project_id == project_id, MemoirEditingJob.job_type == 'prepare_chapter'))).all()) == 1


async def test_worker_tick_reconciles_credit_before_dispatch(monkeypatch):
    calls = []
    stop = asyncio.Event()
    async def no_policy(db):
        return 0
    async def resume(db):
        calls.append('resume')
    async def advance(db):
        calls.append('advance')
    async def dispatch(**kwargs):
        calls.append('dispatch')
        stop.set()
    monkeypatch.setattr(automatic_book_draft, 'reconcile_books_omitted_by_editorial_review', no_policy)
    monkeypatch.setattr(editing_jobs, 'resume_credit_blocked_editing_jobs', resume)
    monkeypatch.setattr('app.services.memoir.book_completion_jobs.advance_book_completion_jobs', advance)
    monkeypatch.setattr(editing_jobs, 'run_available_editing_jobs', dispatch)
    await editing_jobs.editing_worker_loop(stop)
    assert calls == ['resume', 'advance', 'dispatch']


async def test_review_credit_failure_resumes_without_rewriting_ready_chapters(client, test_db, monkeypatch):
    project_id, chapter_id, _, fake, parent_id = await _setup(client, test_db, monkeypatch)
    user_id = (await test_db.get(Project, project_id)).user_id
    await _wallet(test_db, user_id, '5')
    async def unavailable_copy(**kwargs):
        return None
    monkeypatch.setattr('app.services.memoir.book_outputs.generate_manuscript_editorial_copy', unavailable_copy)
    await run_test_editing_job(test_db, parent_id)
    parent = await test_db.get(MemoirEditingJob, parent_id)
    assert await run_test_editing_job(test_db, parent.result_json['chapters'][chapter_id]['job_id'])
    await run_test_editing_job(test_db, parent_id)
    review_id = parent.result_json['review_job_id']
    version_id = parent.result_json['chapters'][chapter_id]['draft_version_id']
    await _wallet(test_db, user_id, '0')
    assert not await run_test_editing_job(test_db, review_id)
    review = await test_db.get(MemoirEditingJob, review_id)
    assert review.result_json['failure']['error_type'] == 'InsufficientAICreditError'
    await run_test_editing_job(test_db, parent_id)
    await add_ai_credit(test_db, user_id=user_id, amount_rmb=Decimal('5'), kind='recharge', actor_user_id=user_id)
    assert await editing_jobs.resume_credit_blocked_editing_jobs(test_db) == 1
    before = len(fake.prompts)
    assert await run_test_editing_job(test_db, review_id)
    assert await run_test_editing_job(test_db, parent_id)
    assert parent.result_json['stage'] == 'ready'
    assert parent.result_json['chapters'][chapter_id]['draft_version_id'] == version_id
    assert len(fake.prompts) == before + 1


async def test_duplicate_payment_delivery_does_not_enqueue_duplicate_work(client, test_db):
    from app.models.ai_credits import AICreditOrder
    from app.services.ai_credit_orders import deliver_credit_order
    project_id = await ensure_mobile_memoir_project(client)
    user_id = (await test_db.get(Project, project_id)).user_id
    account = await _wallet(test_db, user_id, '0')
    await _blocked(test_db, project_id)
    order = AICreditOrder(account_id=account.id, user_id=user_id, out_trade_no=uuid4().hex,
        product_id='9_9_0', price_fen=990, credit_rmb=Decimal('9.9'), status='pending')
    test_db.add(order)
    await test_db.commit()
    order_no = order.out_trade_no
    assert await deliver_credit_order(test_db, out_trade_no=order_no, product_id='9_9_0', wx_order_id='offline') == 'credited'
    assert await editing_jobs.resume_credit_blocked_editing_jobs(test_db) == 1
    assert await deliver_credit_order(test_db, out_trade_no=order_no, product_id='9_9_0', wx_order_id='offline') == 'already_paid'
    assert await editing_jobs.resume_credit_blocked_editing_jobs(test_db) == 0


async def test_two_workers_claim_credit_recovery_only_once(client, test_db, test_engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker
    project_id = await ensure_mobile_memoir_project(client)
    user_id = (await test_db.get(Project, project_id)).user_id
    await _wallet(test_db, user_id, '5')
    await _blocked(test_db, project_id)
    sessions = async_sessionmaker(test_engine, expire_on_commit=False)
    async def recover():
        async with sessions() as db:
            return await editing_jobs.resume_credit_blocked_editing_jobs(db)
    assert sorted(await asyncio.gather(recover(), recover())) == [0, 1]
