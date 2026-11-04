import copy
import json
from dataclasses import replace

from sqlalchemy import select
import pytest

from app.models.memoir import MemoirBookOutput, MemoirBookOutputVersion, MemoirEditingJob
from app.models.projects import Project
from app.schemas.memoir_book import (
    MemoirBookOutputChapterEditorialCopyUpdateRequest,
    MemoirBookOutputEditorialCopyRegenerateRequest,
    MemoirBookOutputUpdateRequest,
)
from app.services.memoir import book_outputs, book_reviews
from app.services.memoir.book_manuscript_ai import GeneratedChapterIntro, GeneratedManuscriptCopy
from tests.memoir_helpers import ensure_mobile_memoir_project, run_test_editing_job as run_job
from tests.test_memoir_book_outputs import _create_confirmed_book_plan, _seed_confirmed_book_output
from tests.test_memoir_automatic_book_draft import _setup


async def run_test_editing_job(db, job_id):
    return await run_job(db, job_id, advance_editorial_copy=False)


def _copy(snapshot, number=1):
    return GeneratedManuscriptCopy(
        book_title='', cover_subtitle='', preface=f'自动前言{number}', afterword=f'自动后记{number}',
        chapter_intros=[GeneratedChapterIntro(chapter_id=item['chapter_id'], intro=f'自动导语{number}') for item in snapshot],
        back_cover_quote='', model='offline-replay', prompt='',
    )


async def _output(client, db):
    project_id = await ensure_mobile_memoir_project(client)
    _, chapters = await _create_confirmed_book_plan(db, project_id=project_id)
    version = await _seed_confirmed_book_output(db, project_id=project_id)
    project = await db.get(Project, project_id)
    return project, chapters, version


async def _automatic(db, project):
    return await book_outputs.regenerate_book_output_editorial_copy(
        db, project_id=project.id, user_id=project.user_id,
        req=MemoirBookOutputEditorialCopyRegenerateRequest(), automatic=True,
    )


@pytest.mark.parametrize('unavailable', [False, True])
async def test_automatic_copy_runs_once_and_failure_keeps_readable_book(client, test_db, monkeypatch, unavailable):
    project, _, before = await _output(client, test_db)
    calls = []
    async def generate(**kwargs):
        calls.append(kwargs)
        return None if unavailable else _copy(kwargs['snapshot'])
    monkeypatch.setattr(book_outputs, 'generate_manuscript_editorial_copy', generate)
    first = await _automatic(test_db, project)
    legacy = await book_outputs._load_output(test_db, project_id=project.id)
    metadata = copy.deepcopy(legacy.metadata_json)
    metadata['automatic_editorial_copy'].pop('ownership_version')
    legacy.metadata_json = metadata
    await test_db.commit()
    second = await _automatic(test_db, project)
    assert len(calls) == 1
    assert first.latest_version.id == second.latest_version.id
    assert second.latest_version.content == before.content
    assert (await test_db.get(MemoirBookOutputVersion, second.latest_version.id)).chapter_snapshot_json == before.chapter_snapshot_json
    if unavailable:
        assert second.latest_version.id == before.id
    else:
        assert second.latest_version.manuscript.chapters[0].intro == '自动导语1'
    output = await test_db.scalar(select(MemoirBookOutput).where(MemoirBookOutput.project_id == project.id))
    assert output.metadata_json['automatic_editorial_copy']['status'] == ('failed' if unavailable else 'succeeded')
    assert output.metadata_json['automatic_editorial_copy']['ownership_version'] == 1


async def test_automatic_copy_preserves_manual_fields_across_later_content_passes(client, test_db, monkeypatch):
    project, chapters, _ = await _output(client, test_db)
    await book_outputs.update_book_output(test_db, user_id=project.user_id, project_id=project.id,
        req=MemoirBookOutputUpdateRequest(preface='自己写的前言'))
    await book_outputs.update_book_output_chapter_editorial_copy(test_db, user_id=project.user_id,
        project_id=project.id, chapter_id=chapters[0].id,
        req=MemoirBookOutputChapterEditorialCopyUpdateRequest(intro='自己写的导语', pull_quote=''))
    calls = []
    async def generate(**kwargs):
        calls.append(kwargs)
        return _copy(kwargs['snapshot'], len(calls))
    monkeypatch.setattr(book_outputs, 'generate_manuscript_editorial_copy', generate)
    await _automatic(test_db, project)
    # A later user edit remains owned even after an automatic version was saved.
    await book_outputs.update_book_output(test_db, user_id=project.user_id, project_id=project.id,
        req=MemoirBookOutputUpdateRequest(afterword='自己写的后记', title='新的书名'))
    result = await _automatic(test_db, project)
    manuscript = result.latest_version.manuscript
    assert next(s.content for s in manuscript.front_matter if s.kind == 'preface') == '自己写的前言'
    assert manuscript.back_matter.afterword.content == '自己写的后记'
    assert manuscript.chapters[0].intro == '自己写的导语'
    assert manuscript.chapters[0].pull_quotes == []
    assert manuscript.chapters[1].intro == '自动导语1'
    assert 'preface' not in calls[-1]['targets'] and 'afterword' not in calls[-1]['targets']
    assert 'chapter_intros' not in calls[-1]['targets']


async def test_automatic_copy_does_not_overwrite_an_edit_during_generation(client, test_db, monkeypatch):
    project, _, _ = await _output(client, test_db)
    async def generate(**kwargs):
        await book_outputs.update_book_output(test_db, user_id=project.user_id, project_id=project.id,
            req=MemoirBookOutputUpdateRequest(preface='刚刚保存的前言'))
        return _copy(kwargs['snapshot'])
    monkeypatch.setattr(book_outputs, 'generate_manuscript_editorial_copy', generate)
    result = await _automatic(test_db, project)
    assert next(s.content for s in result.latest_version.manuscript.front_matter if s.kind == 'preface') == '刚刚保存的前言'
    assert result.latest_version.created_by == 'user'


async def test_requested_ai_copy_predating_automation_is_also_preserved(client, test_db, monkeypatch):
    project, _, _ = await _output(client, test_db)
    calls = []
    async def generate(**kwargs):
        calls.append(kwargs)
        return _copy(kwargs['snapshot'], len(calls))
    monkeypatch.setattr(book_outputs, 'generate_manuscript_editorial_copy', generate)
    await book_outputs.regenerate_book_output_editorial_copy(test_db, user_id=project.user_id, project_id=project.id,
        req=MemoirBookOutputEditorialCopyRegenerateRequest(targets=['preface']))
    result = await _automatic(test_db, project)
    assert next(s.content for s in result.latest_version.manuscript.front_matter if s.kind == 'preface') == '自动前言1'
    assert result.latest_version.manuscript.chapters[0].intro == '自动导语2'


@pytest.mark.parametrize('explicit_retry', [False, True, 'legacy'])
@pytest.mark.parametrize('copy_after_parent', [False, True])
async def test_book_pipeline_dispatches_copy_and_reviews_changed_metadata_once(client, test_db, monkeypatch, explicit_retry, copy_after_parent):
    from app.services.memoir import automatic_book_draft
    project_id, chapter_id, _, _, parent_id = await _setup(client, test_db, monkeypatch)
    if explicit_retry == 'legacy':
        parent = await test_db.get(MemoirEditingJob, parent_id)
        parent.result_json = {'retry_stages': ['chapters', 'editorial_copy', 'review']}
        await test_db.commit()
    elif explicit_retry:
        await automatic_book_draft.schedule_book_draft(test_db, project_id=project_id, source='retry')
        await test_db.commit()
    calls = []
    async def generate(**kwargs):
        calls.append(kwargs)
        return _copy(kwargs['snapshot'], 2 if kwargs['targets'] == ['chapter_intros'] else 1)
    monkeypatch.setattr(book_outputs, 'generate_manuscript_editorial_copy', generate)
    assert not await run_test_editing_job(test_db, parent_id)
    parent = await test_db.get(MemoirEditingJob, parent_id)
    assert await run_test_editing_job(test_db, parent.result_json['chapters'][chapter_id]['job_id'])
    assert not await run_test_editing_job(test_db, parent_id)
    assert not calls
    copy_job = await test_db.scalar(select(MemoirEditingJob).where(
        MemoirEditingJob.dedupe_key == f"automatic-editorial-copy:{project_id}"))
    assert copy_job.payload_json['retry'] is bool(explicit_retry)
    copy_id = copy_job.id
    assert parent.result_json['review_job_id'] is None
    assert parent.result_json['stage'] == 'writing_copy'
    if copy_after_parent:
        assert not await run_test_editing_job(test_db, parent_id)
        assert parent.result_json['review_job_id'] is None
    assert await run_test_editing_job(test_db, copy_id)
    await test_db.refresh(parent)
    assert parent.status == 'pending'
    # The first review sees final copy; there is no review of intermediate metadata.
    assert not await run_test_editing_job(test_db, parent_id)
    review_id = parent.result_json['review_job_id']
    assert await run_test_editing_job(test_db, review_id)
    assert await run_test_editing_job(test_db, parent_id)
    await test_db.refresh(parent)
    await test_db.refresh(copy_job)
    assert parent.result_json['review_job_id'] == review_id
    assert copy_job.status == 'succeeded' and len(calls) == 1
    assert not copy_job.rerun_requested
    assert await book_reviews.get_current_book_review(test_db, project_id=project_id) is not None
    await automatic_book_draft.schedule_book_draft(test_db, project_id=project_id)
    await test_db.commit()
    assert await run_test_editing_job(test_db, parent_id)
    assert copy_job.status == 'succeeded' and parent.result_json['review_job_id'] == review_id
    # Identical book copy and a changed chapter intro must not wake the parent.
    project = await test_db.get(Project, project_id)
    for targets in (['preface', 'afterword'], ['chapter_intros']):
        await book_outputs.regenerate_book_output_editorial_copy(test_db, user_id=project.user_id, project_id=project_id,
            req=MemoirBookOutputEditorialCopyRegenerateRequest(targets=targets))
        await test_db.refresh(parent)
        assert parent.status == 'succeeded'


@pytest.mark.db_independent
@pytest.mark.parametrize('legacy_retry,child_dispatched', [(False, False), (True, False), (False, True)])
async def test_copy_completion_and_running_parent_share_lock_order(client, test_db, monkeypatch, legacy_retry, child_dispatched):
    import asyncio
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.services.memoir import chapter_preparation_jobs, editing_jobs, sections
    from tests.test_memoir_continuous_book import _add_narration
    pid, chapter_id, section_id, fake, parent_id = await _setup(client, test_db, monkeypatch)
    assert not await run_test_editing_job(test_db, parent_id)
    parent = await test_db.get(MemoirEditingJob, parent_id)
    assert await run_test_editing_job(test_db, parent.result_json['chapters'][chapter_id]['job_id'])
    assert not await run_test_editing_job(test_db, parent_id)
    if legacy_retry:
        parent.result_json = {**parent.result_json, 'retry_stages': ['editorial_copy']}
    queued, release = asyncio.Event(), asyncio.Event()
    parent_pid = None
    if child_dispatched:
        await _add_narration(client, test_db, pid, section_id, fake)
        enqueue = chapter_preparation_jobs.enqueue_chapter_preparation_job
        async def enqueue_and_pause(*args, **kwargs):
            nonlocal parent_pid
            result = await enqueue(*args, **kwargs)
            # Dispatch retains the directory lock until the parent checkpoint.
            parent_pid = await args[0].scalar(text('SELECT pg_backend_pid()'))
            queued.set()
            await release.wait()
            return result
        monkeypatch.setattr(chapter_preparation_jobs, 'enqueue_chapter_preparation_job', enqueue_and_pause)
    project = await test_db.get(Project, pid)
    user_id, run_after = project.user_id, parent.run_after
    await test_db.commit()
    task = None
    async with AsyncSession(test_db.bind, expire_on_commit=False) as copy_db, AsyncSession(test_db.bind, expire_on_commit=False) as parent_db:
        lease = await editing_jobs.claim_editing_job(parent_db, parent_id, now=run_after)
        assert lease is not None
        parent_pid = await parent_db.scalar(text('SELECT pg_backend_pid()'))
        if child_dispatched:
            task = asyncio.create_task(editing_jobs.run_editing_job(parent_db, lease))
        async def generate(**kwargs):
            nonlocal task
            async def wait_until_blocked(connection_pid):
                while not await test_db.scalar(text('SELECT cardinality(pg_blocking_pids(:pid)) > 0'), {'pid': connection_pid}):
                    await asyncio.sleep(0.01)
            if child_dispatched:
                # The parent retains its lock through child dispatch. Copy
                # completion waits until that atomic checkpoint is committed.
                await asyncio.wait_for(queued.wait(), timeout=3)
                copy_pid = await copy_db.scalar(text('SELECT pg_backend_pid()'))
                lock_task = asyncio.create_task(sections.lock_project_chapters(copy_db, project_id=pid))
                try:
                    await asyncio.wait_for(wait_until_blocked(copy_pid), timeout=3)
                    release.set()
                    await asyncio.wait_for(lock_task, timeout=3)
                finally:
                    if not lock_task.done():
                        lock_task.cancel()
                    await asyncio.gather(lock_task, return_exceptions=True)
            else:
                await sections.lock_project_chapters(copy_db, project_id=pid)
                task = asyncio.create_task(editing_jobs.run_editing_job(parent_db, lease))
                await asyncio.wait_for(wait_until_blocked(parent_pid), timeout=3)
            return _copy(kwargs['snapshot'])
        monkeypatch.setattr(book_outputs, 'generate_manuscript_editorial_copy', generate)
        try:
            output = await asyncio.wait_for(book_outputs.regenerate_book_output_editorial_copy(
                copy_db, user_id=user_id, project_id=pid,
                req=MemoirBookOutputEditorialCopyRegenerateRequest(), automatic=True,
            ), timeout=5)
            afterword = output.latest_version.manuscript.back_matter.afterword.content
            if child_dispatched:
                # Parent assembly advanced the output while copy waited for
                # its lock; the old copy response must not overwrite it.
                assert afterword != '自动后记1'
            else:
                assert afterword == '自动后记1'
            assert task is not None and not await asyncio.wait_for(task, timeout=5)
            await test_db.refresh(parent)
            assert parent.status == 'pending'
            assert parent.result_json['review_job_id'] is None
        finally:
            release.set()
            await copy_db.rollback()
            if task is not None:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)


async def test_first_historical_user_version_is_preserved_without_spending_model_calls(client, test_db, monkeypatch):
    project, _, version = await _output(client, test_db)
    version.created_by = 'user'
    await test_db.commit()
    async def unexpected(**kwargs):
        pytest.fail('All legacy copy was manually supplied')
    monkeypatch.setattr(book_outputs, 'generate_manuscript_editorial_copy', unexpected)
    result = await _automatic(test_db, project)
    assert result.latest_version.id == version.id


@pytest.mark.parametrize('edit_kind', ['user', 'requested_ai'])
async def test_saved_copy_and_supplement_finish_automatic_book_together(client, test_db, monkeypatch, edit_kind):
    from app.services.memoir import draft_progress
    from tests.test_memoir_continuous_book import _add_narration, _ready_book

    calls = []
    async def generate(**kwargs):
        calls.append(kwargs)
        result = _copy(kwargs['snapshot'], len(calls))
        if kwargs["targets"] == ["pull_quotes"]:
            return replace(result, chapter_intros=[replace(chapter, valid_empty_quote=True)
                for chapter in result.chapter_intros])
        return result
    monkeypatch.setattr(book_outputs, 'generate_manuscript_editorial_copy', generate)
    project_id, chapter_id, section_id, fake, parent = await _ready_book(client, test_db, monkeypatch)
    parent_id = parent.id
    user_id = (await test_db.get(Project, project_id)).user_id
    await book_outputs.update_book_output(test_db, user_id=user_id, project_id=project_id,
        req=MemoirBookOutputUpdateRequest(preface='留给家人的前言'))
    if edit_kind == 'user':
        saved = await book_outputs.update_book_output_chapter_editorial_copy(
            test_db, user_id=user_id, project_id=project_id, chapter_id=chapter_id,
            req=MemoirBookOutputChapterEditorialCopyUpdateRequest(intro='我的学徒岁月', pull_quote=''))
    else:
        saved = await book_outputs.regenerate_book_output_editorial_copy(
            test_db, user_id=user_id, project_id=project_id,
            req=MemoirBookOutputEditorialCopyRegenerateRequest(targets=['chapter_intros']))
    original = saved.latest_version.manuscript.chapters[0]
    previous_calls = len(calls)
    await _add_narration(client, test_db, project_id, section_id, fake)
    assert not await run_test_editing_job(test_db, parent_id)
    parent = await test_db.get(MemoirEditingJob, parent_id)
    assert await run_test_editing_job(test_db, parent.result_json['chapters'][chapter_id]['job_id'])
    assert not await run_test_editing_job(test_db, parent_id)
    parent = await test_db.get(MemoirEditingJob, parent_id)
    assert parent.result_json['review_job_id'] is None
    copy_job = await test_db.scalar(select(MemoirEditingJob).where(
        MemoirEditingJob.dedupe_key == f"automatic-editorial-copy:{project_id}"))
    assert await run_test_editing_job(test_db, copy_job.id)
    assert not await run_test_editing_job(test_db, parent_id)
    assert await run_test_editing_job(test_db, parent.result_json['review_job_id'])
    assert await run_test_editing_job(test_db, parent_id)
    progress = await draft_progress.read_draft_progress(test_db, project_id=project_id)
    assert progress.status == 'ready'
    current = await book_outputs.get_book_output(test_db, user_id=user_id, project_id=project_id)
    assert all(set(call["targets"]) <= {"chapter_intros", "pull_quotes"} for call in calls[previous_calls:])
    assert not current.is_stale
    assert current.latest_version.id != saved.latest_version.id
    manuscript = current.latest_version.manuscript
    assert manuscript.chapters[0].intro == original.intro
    if edit_kind == 'user':
        assert manuscript.chapters[0].pull_quotes == []
    assert next(s.content for s in manuscript.front_matter if s.kind == 'preface') == '留给家人的前言'
    before_ids = {ref.utterance_id for ref in original.source_refs}
    after_ids = {ref.utterance_id for ref in manuscript.chapters[0].source_refs}
    assert before_ids < after_ids


@pytest.mark.parametrize('failed_update', [False, True])
async def test_only_changed_chapter_copy_is_regenerated(client, test_db, monkeypatch, failed_update):
    from app.domain.memoir.types import MemoirCreatedBy
    from app.services.memoir.book_output_versions import append_book_output_version
    from tests.memoir_helpers import create_test_book_plan_with_chapters

    project_id = await ensure_mobile_memoir_project(client)
    _, chapters = await create_test_book_plan_with_chapters(test_db, project_id=project_id,
        chapter_titles=[f'往事{i}' for i in range(5)], draft_contents=[f'这是第{i}段经历。' for i in range(5)])
    await _seed_confirmed_book_output(test_db, project_id=project_id)
    project = await test_db.get(Project, project_id)
    calls = []
    async def generate(**kwargs):
        calls.append(kwargs)
        generated = _copy(kwargs['snapshot'], len(calls))
        if failed_update and len(calls) == 2:
            return None
        return generated
    monkeypatch.setattr(book_outputs, 'generate_manuscript_editorial_copy', generate)
    first = await _automatic(test_db, project)
    output = await test_db.scalar(select(MemoirBookOutput).where(MemoirBookOutput.project_id == project_id))
    latest = await test_db.get(MemoirBookOutputVersion, first.latest_version.id)
    snapshot = copy.deepcopy(latest.chapter_snapshot_json)
    snapshot[-1]['content'] += '后来又发生了一件事。'
    await append_book_output_version(test_db, output=output, content=latest.content,
        chapter_snapshot_json=snapshot, manuscript_json=latest.manuscript_json, created_by=MemoirCreatedBy.SYSTEM)
    await test_db.commit()
    changed = await _automatic(test_db, project)
    assert calls[-1]['chapter_ids'] == [chapters[-1].id]
    if failed_update:
        await _automatic(test_db, project)
        assert len(calls) == 2
        changed = await book_outputs.regenerate_book_output_editorial_copy(test_db, user_id=project.user_id, project_id=project_id,
            req=MemoirBookOutputEditorialCopyRegenerateRequest(), automatic=True, retry=True)
        assert calls[-1]['chapter_ids'] == [chapters[-1].id]
    assert [chapter.intro for chapter in changed.latest_version.manuscript.chapters] == ['自动导语1'] * 4 + [f'自动导语{3 if failed_update else 2}']
    await book_outputs.regenerate_book_output_editorial_copy(test_db, user_id=project.user_id, project_id=project_id,
        req=MemoirBookOutputEditorialCopyRegenerateRequest(), automatic=True, retry=True)
    assert len(calls) == (3 if failed_update else 2)  # Retrying another stage does not rewrite successful copy.


async def test_audit_changes_do_not_regenerate_editorial_copy(client, test_db, monkeypatch):
    from app.domain.memoir.types import MemoirCreatedBy
    from app.services.memoir.book_output_versions import append_book_output_version

    project, chapters, before = await _output(client, test_db)
    snapshot = copy.deepcopy(before.chapter_snapshot_json)
    for item in snapshot:
        item['source_refs'] = [{'utterance_id': f"source-{item['chapter_id']}", 'quote': item["content"],
            'transcript_version_id': 'original-transcript', 'voice_session_id': 'original-session'}]
        for section in item.get("sections", []):
            section["used_material_ids"] = [item["source_refs"][0]["utterance_id"]]
    before.chapter_snapshot_json = snapshot
    await test_db.commit()
    calls = []
    async def generate(**kwargs):
        calls.append(kwargs)
        return _copy(kwargs['snapshot'], len(calls))
    monkeypatch.setattr(book_outputs, 'generate_manuscript_editorial_copy', generate)
    first = await _automatic(test_db, project)
    output = await test_db.scalar(select(MemoirBookOutput).where(MemoirBookOutput.project_id == project.id))
    latest = await test_db.get(MemoirBookOutputVersion, first.latest_version.id)
    snapshot = copy.deepcopy(latest.chapter_snapshot_json)
    for item in snapshot:
        item['metadata_json'] = {**item['metadata_json'], 'audit_record': {'updated_at': 'later', 'attempts': 2}}
        item['draft_version_id'] = 'new-version-with-same-content'
        item['source_refs'][0].update(transcript_version_id='corrected-transcript', voice_session_id='archived-session')
    changed = await append_book_output_version(test_db, output=output, content=latest.content,
        chapter_snapshot_json=snapshot, manuscript_json=latest.manuscript_json, created_by=MemoirCreatedBy.SYSTEM)
    await test_db.commit()
    reused = await _automatic(test_db, project)
    assert len(calls) == 1
    assert reused.latest_version.id == changed.id
    assert [chapter.intro for chapter in reused.latest_version.manuscript.chapters] == ['自动导语1'] * len(chapters)
    # A real source correction still updates the affected chapter, even when body is unchanged.
    corrected = copy.deepcopy(snapshot)
    corrected[-1]['source_refs'][0]['quote'] = '那时我已经到外地读书。'
    await append_book_output_version(test_db, output=output, content=latest.content,
        chapter_snapshot_json=corrected, manuscript_json=latest.manuscript_json, created_by=MemoirCreatedBy.SYSTEM)
    await test_db.commit()
    await _automatic(test_db, project)
    assert len(calls) == 2 and calls[-1]['chapter_ids'] == [chapters[-1].id]
    project.title = '改过的书名'
    await test_db.commit()
    renamed = await _automatic(test_db, project)
    assert len(calls) == 3 and calls[-1]['chapter_ids'] == []
    assert 'chapter_intros' not in calls[-1]['targets']
    latest = await test_db.get(MemoirBookOutputVersion, renamed.latest_version.id)
    await append_book_output_version(test_db, output=output, content=latest.content,
        chapter_snapshot_json=list(reversed(corrected)), manuscript_json=latest.manuscript_json,
        created_by=MemoirCreatedBy.SYSTEM)
    await test_db.commit()
    await _automatic(test_db, project)
    assert len(calls) == 3  # Reordering chapters reviews the reading order but preserves copy.
    assert 'chapter_intros' not in calls[-1]['targets']


@pytest.mark.parametrize('remove_selected_quote', [False, True])
async def test_source_removal_preserves_authored_intro_and_only_valid_quotes(client, test_db, monkeypatch, remove_selected_quote):
    from app.models.memoir import MemoirBookChapterDraftVersion, MemoirSectionMaterial
    from app.services.memoir.narrative.book_output_assembly import assemble_book_output
    from tests.test_memoir_book_outputs import _add_output_source_link

    project_id = await ensure_mobile_memoir_project(client)
    plan, chapters = await _create_confirmed_book_plan(test_db, project_id=project_id)
    for index in (1, 2):
        await _add_output_source_link(test_db, project_id=project_id, plan_id=plan.id, chapter_id=chapters[0].id,
            utterance_id=f'quote-{index}', quote=f'这是第{index}条真实讲述。')
    await test_db.commit()
    await _seed_confirmed_book_output(test_db, project_id=project_id)
    project = await test_db.get(Project, project_id)
    async def generate(**kwargs): return _copy(kwargs['snapshot'])
    monkeypatch.setattr(book_outputs, 'generate_manuscript_editorial_copy', generate)
    await _automatic(test_db, project)
    saved = await book_outputs.update_book_output_chapter_editorial_copy(test_db, user_id=project.user_id,
        project_id=project_id, chapter_id=chapters[0].id,
        req=MemoirBookOutputChapterEditorialCopyUpdateRequest(intro='这些是我在外婆家的日子。',
            pull_quote='这是第1条真实讲述。', pull_quote_source_ref_id='quote-1'))
    removed = 'quote-1' if remove_selected_quote else 'quote-2'
    link = await test_db.scalar(select(MemoirSectionMaterial).where(MemoirSectionMaterial.utterance_id == removed))
    await test_db.delete(link)
    draft = await test_db.scalar(select(MemoirBookChapterDraftVersion).where(
        MemoirBookChapterDraftVersion.chapter_id == chapters[0].id))
    draft.content += '只保留仍收录的讲述。'
    await test_db.commit()
    await assemble_book_output(test_db, project_id=project_id, automatic=True)
    current = await _automatic(test_db, project)
    chapter = current.latest_version.manuscript.chapters[0]
    assert chapter.intro == '这些是我在外婆家的日子。'
    assert [quote.text for quote in chapter.pull_quotes] == ([] if remove_selected_quote else ['这是第1条真实讲述。'])
    assert (await test_db.get(MemoirBookOutputVersion, saved.latest_version.id)).manuscript_json['chapters'][0]['intro'] == chapter.intro
    assert (await _automatic(test_db, project)).latest_version.id == current.latest_version.id


async def test_copy_failure_is_retryable_without_rewriting_ready_chapters(client, test_db, monkeypatch):
    from app.services.memoir import draft_progress
    from tests.test_memoir_continuous_book import _ready_book
    calls = []
    async def generate(**kwargs):
        calls.append(kwargs)
        return None if len(calls) == 1 else _copy(kwargs['snapshot'])
    monkeypatch.setattr(book_outputs, 'generate_manuscript_editorial_copy', generate)
    project_id, chapter_id, _, _, parent = await _ready_book(client, test_db, monkeypatch)
    parent_id = parent.id
    original_draft = parent.result_json['chapters'][chapter_id]['draft_version_id']
    progress = await draft_progress.read_draft_progress(test_db, project_id=project_id)
    assert progress.status == 'failed' and progress.can_retry
    await draft_progress.retry_draft_progress(test_db, project_id=project_id)
    copy_job = await test_db.scalar(select(MemoirEditingJob).where(
        MemoirEditingJob.dedupe_key == f"automatic-editorial-copy:{project_id}"))
    assert parent.status == 'succeeded'
    assert await run_test_editing_job(test_db, copy_job.id)
    assert not await run_test_editing_job(test_db, parent_id)
    assert await run_test_editing_job(test_db, parent.result_json['review_job_id'])
    assert await run_test_editing_job(test_db, parent_id)
    assert parent.result_json['chapters'][chapter_id]['draft_version_id'] == original_draft
    assert len(calls) == 2
    progress = await draft_progress.read_draft_progress(test_db, project_id=project_id)
    assert progress.status == 'ready' and not progress.can_retry


@pytest.mark.ci_smoke
@pytest.mark.parametrize("body, expected", [
    ("我五岁那年发过洪水。爹挑着我们上山。", "爹挑着我们上山。"),
    ("并不是我十五岁那年发过洪水。", ""),
])
def test_corrected_body_controls_copy_and_original_quotes(body, expected):
    from app.services.memoir.book_manuscript_ai import build_manuscript_copy_prompt, _copy_from_payload
    from app.services.memoir.book_manuscript_cleanup import original_quote_candidates
    from app.services.memoir.book_output_versions import retained_chapter_quotes
    source = "我十五岁那年发过洪水。爹挑着我们上山。"
    ref = {"utterance_id": "u1", "quote": source}
    snapshot = {"chapter_id": "c1", "chapter_title": "洪水", "content": body,
                "chapter_summary": source, "source_refs": [{**ref, "summary": source}],
                "sections": [{"section_id": "s1", "content": body, "used_material_ids": ["u1"]}]}
    assert original_quote_candidates(snapshot, max_chars=90)["u1"] == ((expected,) if expected else ())
    prompt = build_manuscript_copy_prompt(title="往事", snapshot=[snapshot])
    # Old summaries and recordings cannot compete with the corrected body.
    assert source not in prompt
    assert body in prompt
    generated = _copy_from_payload({"preface": "家里发过洪水。",
        "chapter_intros": [{"chapter_id": "C1", "intro": "家里发过洪水。",
                           "pull_quote": "我十五岁那年发过洪水。", "pull_quote_source_ref_id": "M1"}]},
        snapshot=[snapshot], prompt=prompt, model="offline")
    assert generated.chapter_intros[0].pull_quote == ""
    old = {**snapshot, "content": source, "pull_quotes": [
        {"text": "我十五岁那年发过洪水。", "source_ref_ids": ["u1"], "source": "original_quote"}]}
    # Automatic originals cannot contradict a corrected body, even in LOW.
    from app.domain.memoir.types import MemoirTranscriptPolishLevel
    for level in MemoirTranscriptPolishLevel:
        assert retained_chapter_quotes(old, snapshot, polish_level=level) == []
    assert retained_chapter_quotes(old, snapshot, preserve_manual=True)[0].source == "original_quote"
    invalid_source = {**snapshot, "source_refs": [{**ref, "quote": "我五岁那年发过洪水。"}]}
    assert retained_chapter_quotes(old, invalid_source, preserve_manual=True) == []
    old["pull_quotes"][0]["source"] = "manual"
    assert retained_chapter_quotes(old, snapshot)[0].source == "manual"


def test_quote_cannot_borrow_a_matching_sentence_from_another_material():
    from app.services.memoir.book_manuscript_cleanup import original_quote_candidates
    source = "十五岁那年我去北京。"
    snapshot = {"content": "五岁那年我去北京。" + source, "sections": [
        {"content": "五岁那年我去北京。", "used_material_ids": ["u1"]},
        {"content": source, "used_material_ids": ["u2"]},
    ]}
    snapshot["source_refs"] = [{"utterance_id": key, "quote": source} for key in ("u1", "u2")]
    assert original_quote_candidates(snapshot, max_chars=90)["u1"] == ()
    assert original_quote_candidates(snapshot, max_chars=90)["u2"] == (source,)


def test_edited_body_fallback_never_restores_old_recording_in_cover_copy():
    from app.services.memoir.book_output_manuscript import manuscript_from_snapshot
    old = "我十五岁那年家里发洪水。"
    body = "我五岁那年家里发洪水。"
    manuscript = manuscript_from_snapshot(title="往事", content=body, snapshot=[{
        "chapter_id": "c1", "chapter_title": "洪水", "content": body,
        "source_refs": [{"utterance_id": "u1", "quote": old}],
    }])
    assert manuscript.chapters[0].pull_quotes == []
    assert old not in manuscript.render_settings.back_cover_quote


async def test_assembly_with_new_body_drops_obsolete_automatic_preface_before_copy_job(client, test_db, monkeypatch):
    from app.models.memoir import MemoirBookChapterDraftVersion
    from app.services.memoir.narrative.book_output_assembly import assemble_book_output
    project, chapters, _ = await _output(client, test_db)
    async def generate(**kwargs):
        return replace(_copy(kwargs["snapshot"]), preface="十五岁那年发生洪水。")
    monkeypatch.setattr(book_outputs, "generate_manuscript_editorial_copy", generate)
    await _automatic(test_db, project)
    version = await test_db.scalar(select(MemoirBookChapterDraftVersion).where(
        MemoirBookChapterDraftVersion.chapter_id == chapters[0].id))
    version.content = "五岁那年发生洪水。"
    sections = copy.deepcopy(version.section_snapshot_json)
    sections[0]["content"] = version.content
    version.section_snapshot_json = sections
    await test_db.commit()
    assembled = await assemble_book_output(test_db, project_id=project.id, automatic=True)
    output = await test_db.get(MemoirBookOutputVersion, assembled.output_version_id)
    preface = next(s["content"] for s in output.manuscript_json["front_matter"] if s["kind"] == "preface")
    assert "十五岁" not in preface and "15岁" not in preface
    assert "五岁" in output.content


@pytest.mark.parametrize("old_version", [0, 1])
async def test_old_editorial_policy_refreshes_once_without_claiming_manual_copy(client, test_db, monkeypatch, old_version):
    project, chapters, _ = await _output(client, test_db)
    calls = []
    async def generate(**kwargs):
        calls.append(kwargs)
        return _copy(kwargs["snapshot"], len(calls))
    monkeypatch.setattr(book_outputs, "generate_manuscript_editorial_copy", generate)
    await _automatic(test_db, project)
    output = await test_db.scalar(select(MemoirBookOutput).where(MemoirBookOutput.project_id == project.id))
    metadata = copy.deepcopy(output.metadata_json)
    metadata["automatic_editorial_copy"].update(signature_version=old_version, input_signature="old-evidence-policy")
    output.metadata_json = metadata
    await test_db.commit()
    await _automatic(test_db, project)
    await _automatic(test_db, project)
    assert len(calls) == 2
    assert set(calls[-1]["chapter_ids"]) == {chapter.id for chapter in chapters}
    assert not output.metadata_json["automatic_editorial_copy"]["manual_targets"]
    assert output.metadata_json["automatic_editorial_copy"]["signature_version"] == 2


@pytest.mark.parametrize("entry", ["manual", "revision", "automatic"])
async def test_new_assembly_schedules_only_copy(client, test_db, entry):
    from app.models.memoir import MemoirBookChapterDraftVersion
    from app.services.memoir.book_revision_generation import _load_baseline
    from app.services.memoir.narrative.book_output_assembly import assemble_book_output
    project, chapters, _ = await _output(client, test_db)
    version = await test_db.scalar(select(MemoirBookChapterDraftVersion).where(
        MemoirBookChapterDraftVersion.chapter_id == chapters[0].id))
    version.content += "后来我们搬到了绵阳。"
    sections = copy.deepcopy(version.section_snapshot_json)
    sections[0]["content"] = version.content
    version.section_snapshot_json = sections
    await test_db.commit()
    if entry == "manual":
        response = await client.post(f"/api/memoir/projects/{project.id}/book-output/assemble")
        assert response.status_code == 200
    elif entry == "revision":
        await _load_baseline(test_db, project_id=project.id, missing_message="缺少正文")
    else:
        await assemble_book_output(test_db, project_id=project.id, automatic=True)
    job = await test_db.scalar(select(MemoirEditingJob).where(
        MemoirEditingJob.dedupe_key == f"automatic-editorial-copy:{project.id}"))
    assert await test_db.scalar(select(MemoirEditingJob).where(
        MemoirEditingJob.dedupe_key == f"update-book-draft:{project.id}")) is None
    if entry == "automatic":
        assert job is None  # The running pipeline already performs its copy stage.
    else:
        assert job is not None and job.status == "pending"
        assert job.payload_json["automatic"] is True
        scheduled_at = job.updated_at
        await assemble_book_output(test_db, project_id=project.id)
        await test_db.flush()
        assert job.updated_at == scheduled_at


@pytest.mark.parametrize("result_kind", ["complete", "unavailable", "missing_intro", "book_title", "cover_subtitle"])
async def test_copy_only_task_preserves_body_and_can_retry_without_full_workflow(client, test_db, monkeypatch, result_kind):
    from app.services.memoir import book_generation_jobs, draft_progress
    from app.services.memoir.editing_job_handlers import register_memoir_editing_job_handlers
    from app.services.memoir.editing_jobs import clear_editing_job_handlers
    project, _, before = await _output(client, test_db)
    unavailable = result_kind not in {"complete", "missing_intro"}
    calls = []
    async def generate(**kwargs):
        calls.append(kwargs)
        generated = _copy(kwargs["snapshot"])
        if result_kind == "missing_intro":
            generated = replace(generated, chapter_intros=[
                replace(item, intro="") if index == 0 else item
                for index, item in enumerate(generated.chapter_intros)])
        if unavailable:
            return None if result_kind == "unavailable" else replace(generated,
                preface="", afterword="", chapter_intros=[], **{result_kind: "非请求字段"})
        return generated
    monkeypatch.setattr(book_outputs, "generate_manuscript_editorial_copy", generate)
    await book_generation_jobs.schedule_book_editorial_copy_generation(test_db,
        user_id=project.user_id, project_id=project.id, req=MemoirBookOutputEditorialCopyRegenerateRequest(), automatic=True)
    await test_db.commit()
    job = await test_db.scalar(select(MemoirEditingJob).where(MemoirEditingJob.dedupe_key == f"automatic-editorial-copy:{project.id}"))
    clear_editing_job_handlers()
    register_memoir_editing_job_handlers()
    try:
        assert await run_test_editing_job(test_db, job.id) is (not unavailable)
        await test_db.refresh(job)
        assert job.status == ("failed" if unavailable else "succeeded")
        if unavailable:
            status = await draft_progress.read_draft_progress(test_db, project_id=project.id)
            assert status.can_retry and status.status == "failed"
            unavailable = False
            await draft_progress.retry_draft_progress(test_db, project_id=project.id)
            await test_db.commit()
            assert await run_test_editing_job(test_db, job.id)
            await test_db.refresh(job)
            assert job.status == "succeeded"
        latest = await test_db.scalar(select(MemoirBookOutputVersion).where(
            MemoirBookOutputVersion.output_id == before.output_id).order_by(MemoirBookOutputVersion.version.desc()))
        assert latest.content == before.content
        assert latest.chapter_snapshot_json == before.chapter_snapshot_json
        expected_intro = before.manuscript_json["chapters"][0]["intro"] if result_kind == "missing_intro" else "自动导语1"
        assert latest.manuscript_json["chapters"][0]["intro"] == expected_intro
        assert next(s["content"] for s in latest.manuscript_json["front_matter"] if s["kind"] == "preface") == "自动前言1"
        assert latest.manuscript_json["chapters"][1]["intro"] == "自动导语1"
        if result_kind == "missing_intro":
            reused = await book_outputs.regenerate_book_output_editorial_copy(test_db,
                user_id=project.user_id, project_id=project.id, req=MemoirBookOutputEditorialCopyRegenerateRequest(),
                automatic=True, retry=True)
            assert reused.latest_version.id == latest.id
            assert len(calls) == 1
        jobs = (await test_db.scalars(select(MemoirEditingJob).where(MemoirEditingJob.project_id == project.id))).all()
        assert {item.job_type for item in jobs} == {"generate_book_editorial_copy"}
    finally:
        clear_editing_job_handlers()


@pytest.mark.parametrize("scope", ["other_target", "other_chapter", "protected_intro", "duplicate_chapter"])
async def test_copy_success_requires_an_applicable_requested_field(client, test_db, monkeypatch, scope):
    from fastapi import HTTPException
    project, chapters, _ = await _output(client, test_db)
    automatic = scope == "protected_intro"
    if automatic:
        await book_outputs.update_book_output_chapter_editorial_copy(test_db,
            user_id=project.user_id, project_id=project.id, chapter_id=chapters[0].id,
            req=MemoirBookOutputChapterEditorialCopyUpdateRequest(intro="自己写的导语"))
    req = MemoirBookOutputEditorialCopyRegenerateRequest(targets=["chapter_intros"], chapter_ids=[chapters[0].id])
    calls = []
    async def generate(**kwargs):
        calls.append(kwargs)
        generated = _copy(kwargs["snapshot"])
        if len(calls) > 1:
            return generated
        chapter_id = chapters[1].id if scope == "other_chapter" else chapters[0].id
        if scope == "duplicate_chapter":
            return replace(generated, preface="", afterword="", chapter_intros=[
                GeneratedChapterIntro(chapter_id=chapter_id, intro="被后项覆盖的导语"),
                GeneratedChapterIntro(chapter_id=chapter_id, intro="")])
        return replace(generated, preface="非请求的前言" if scope == "other_target" else "", afterword="",
            chapter_intros=[] if scope == "other_target" else [GeneratedChapterIntro(chapter_id=chapter_id, intro="新导语")])
    monkeypatch.setattr(book_outputs, "generate_manuscript_editorial_copy", generate)
    if automatic:
        first = await book_outputs.regenerate_book_output_editorial_copy(test_db,
            user_id=project.user_id, project_id=project.id, req=req, automatic=True)
        assert first.latest_version.manuscript.chapters[0].intro == "自己写的导语"
        output = await test_db.scalar(select(MemoirBookOutput).where(MemoirBookOutput.project_id == project.id))
        assert output.metadata_json["automatic_editorial_copy"]["status"] == "failed"
    else:
        with pytest.raises(HTTPException) as error:
            await book_outputs.regenerate_book_output_editorial_copy(test_db,
                user_id=project.user_id, project_id=project.id, req=req)
        assert error.value.status_code == 503
    result = await book_outputs.regenerate_book_output_editorial_copy(test_db,
        user_id=project.user_id, project_id=project.id, req=req, automatic=automatic, retry=True)
    assert len(calls) == 2
    assert result.latest_version.manuscript.chapters[1 if automatic else 0].intro == "自动导语1"


@pytest.mark.parametrize("automatic", [False, True])
@pytest.mark.parametrize("reply", ["empty", "missing", "has_candidate"])
async def test_quote_only_job_accepts_explicit_empty_even_with_candidates(client, test_db, monkeypatch, automatic, reply):
    from app.services.memoir import book_generation_jobs
    from app.services.memoir.editing_job_handlers import register_memoir_editing_job_handlers
    from app.services.memoir.editing_jobs import clear_editing_job_handlers
    from tests.memoir_helpers import create_test_book_plan_with_chapters
    from tests.test_memoir_book_outputs import FakeEditorialAI

    project_id = await ensure_mobile_memoir_project(client)
    body = "我五岁那年离开家。" + ("后来我到县城读书，认识了许多新朋友。" if reply == "has_candidate" else "")
    _, chapters = await create_test_book_plan_with_chapters(test_db, project_id=project_id,
        chapter_titles=["离家读书"], draft_contents=[body])
    before = await _seed_confirmed_book_output(test_db, project_id=project_id)
    stored = copy.deepcopy(before.manuscript_json)
    stored["chapters"][0]["pull_quotes"] = [{"text": "已经不在当前正文里的旧摘句。", "source": "editorial_generated", "source_ref_ids": []}]
    before.manuscript_json = stored
    await test_db.commit()
    project = await test_db.get(Project, project_id)
    calls = []
    async def respond(*args, **kwargs):
        calls.append(kwargs)
        item = {"chapter_id": "C1"}
        if reply != "missing":
            item["pull_quote"] = ""
        return json.dumps({"chapter_intros": [item]})
    fake_ai = FakeEditorialAI()
    monkeypatch.setattr(fake_ai, "_call_llm", respond)
    monkeypatch.setattr("app.services.memoir.book_manuscript_ai._text_ai_configured", lambda model: True)
    monkeypatch.setattr("app.services.memoir.book_manuscript_ai.get_ai_service", lambda model: fake_ai)
    req = MemoirBookOutputEditorialCopyRegenerateRequest(targets=["pull_quotes"], chapter_ids=[chapters[0].id])
    await book_generation_jobs.schedule_book_editorial_copy_generation(test_db,
        user_id=project.user_id, project_id=project_id, req=req, automatic=automatic)
    await test_db.commit()
    job = await test_db.scalar(select(MemoirEditingJob).where(MemoirEditingJob.project_id == project_id))
    clear_editing_job_handlers()
    register_memoir_editing_job_handlers()
    try:
        succeeds = reply != "missing"
        assert await run_test_editing_job(test_db, job.id) is succeeds
        await test_db.refresh(job)
        assert job.status == ("succeeded" if succeeds else "failed")
        latest = await test_db.scalar(select(MemoirBookOutputVersion).where(
            MemoirBookOutputVersion.output_id == before.output_id).order_by(MemoirBookOutputVersion.version.desc()))
        assert latest.content == before.content
        assert latest.manuscript_json["chapters"][0]["pull_quotes"] == ([] if succeeds else stored["chapters"][0]["pull_quotes"])
        if succeeds and automatic:
            reused = await book_outputs.regenerate_book_output_editorial_copy(test_db,
                user_id=project.user_id, project_id=project_id, req=req, automatic=True, retry=True)
            assert reused.latest_version.id == latest.id
        assert len(calls) == (2 if reply == "missing" else 1)
    finally:
        clear_editing_job_handlers()


@pytest.mark.parametrize("unavailable", [False, True])
async def test_assembly_copy_runs_independently_and_coalesces_new_input(client, test_db, monkeypatch, unavailable):
    from app.services.memoir import book_generation_jobs, automatic_book_draft, draft_progress
    from app.services.memoir.book_output_versions import revision_compatible_version
    from app.services.memoir.narrative.book_output_assembly import assemble_book_output
    project, _, _ = await _output(client, test_db)
    assembled = await assemble_book_output(test_db, project_id=project.id, automatic=True)
    before = await test_db.get(MemoirBookOutputVersion, assembled.output_version_id)
    req = MemoirBookOutputEditorialCopyRegenerateRequest()
    await book_generation_jobs.schedule_book_editorial_copy_generation(test_db,
        user_id=project.user_id, project_id=project.id, req=req, automatic=True)
    parent = await automatic_book_draft.schedule_book_draft(test_db, project_id=project.id)
    job = await test_db.scalar(select(MemoirEditingJob).where(MemoirEditingJob.dedupe_key == f"automatic-editorial-copy:{project.id}"))
    calls = []
    async def generate(**kwargs):
        calls.append(kwargs)
        return None if unavailable else _copy(kwargs["snapshot"])
    monkeypatch.setattr(book_outputs, "generate_manuscript_editorial_copy", generate)
    result = await book_generation_jobs.run_book_editorial_copy_generation_job(test_db, job)
    assert result.status.value == ("failed" if unavailable else "succeeded") and len(calls) == 1
    parent.status = "succeeded"
    await test_db.commit()
    result = await book_generation_jobs.run_book_editorial_copy_generation_job(test_db, job)
    assert result.status.value == ("failed" if unavailable else "succeeded") and len(calls) == 1
    job.status, job.error_message = result.status.value, result.error_message
    await test_db.commit()
    if unavailable:
        await draft_progress.retry_draft_progress(test_db, project_id=project.id)
        # A new assembly coalesced before execution must retain this user retry.
        await book_generation_jobs.schedule_book_editorial_copy_generation(test_db,
            user_id=project.user_id, project_id=project.id, req=req, automatic=True)
        unavailable = False
        result = await book_generation_jobs.run_book_editorial_copy_generation_job(test_db, job)
        assert result.status.value == "succeeded" and len(calls) == 2
    assert (await revision_compatible_version(test_db, before)).version > before.version
    from app.models.memoir import MemoirAutomationEvent
    events = (await test_db.scalars(select(MemoirAutomationEvent).where(
        MemoirAutomationEvent.editing_job_id == job.id))).all()
    assert len(events) == 1  # Internal scheduling updates one durable event.
    job.status = "running"
    project.title = "新的书名触发新文案输入"
    await test_db.commit()
    await book_generation_jobs.schedule_book_editorial_copy_generation(test_db,
        user_id=project.user_id, project_id=project.id, req=req, automatic=True)
    assert job.rerun_requested  # New assemblies during an AI call cannot lose their copy update.


@pytest.mark.parametrize("manual", [False, True])
@pytest.mark.parametrize("continued_quote", ["none", "paragraphs", "unclosed_tail"])
async def test_old_automatic_opening_quote_repaired_once_without_ai_or_manual_changes(client, test_db, monkeypatch, manual, continued_quote):
    from app.models.memoir import MemoirBookChapterDraftVersion
    from app.services.memoir.narrative.book_output_assembly import assemble_book_output
    from app.services.memoir.book_output_versions import load_latest_book_output_version
    project, chapters, _ = await _output(client, test_db)
    draft = await test_db.scalar(select(MemoirBookChapterDraftVersion).where(MemoirBookChapterDraftVersion.chapter_id == chapters[0].id))
    opening = '那一天我们从县城回到老家。'
    ending = '从那以后，我一直记得师傅教我修车的那些日子。'
    speech = {'none': '', 'paragraphs': '\n母亲说：“孩子，路上小心。\n“到了就写信回来。”\n',
              'unclosed_tail': '母亲说：“孩子，路上小心。'}[continued_quote]
    draft.content = opening + speech + ending
    sections = copy.deepcopy(draft.section_snapshot_json)
    sections[0]['content'] = draft.content
    draft.section_snapshot_json = sections
    await test_db.commit()
    await assemble_book_output(test_db, project_id=project.id, automatic=True)
    calls = []
    async def generate(**kwargs):
        calls.append(kwargs)
        return _copy(kwargs['snapshot'])
    monkeypatch.setattr(book_outputs, 'generate_manuscript_editorial_copy', generate)
    await _automatic(test_db, project)
    output = await test_db.scalar(select(MemoirBookOutput).where(MemoirBookOutput.project_id == project.id))
    latest = await load_latest_book_output_version(test_db, output_id=output.id)
    manuscript = copy.deepcopy(latest.manuscript_json)
    manuscript['chapters'][0]['pull_quotes'] = [{'text': opening, 'source': 'editorial_generated', 'source_ref_ids': []}]
    latest.manuscript_json = manuscript
    if manual:
        metadata = copy.deepcopy(output.metadata_json)
        metadata['automatic_editorial_copy']['manual_chapter_fields'][chapters[0].id] = ['pull_quotes']
        output.metadata_json = metadata
    output.status = 'accepted'
    await test_db.commit()
    before_export = await book_outputs.prepare_book_output_export(test_db, user_id=project.user_id,
        project_id=project.id, export_format=book_outputs.MemoirBookOutputExportFormat.PDF)
    before_id = latest.id
    first = await assemble_book_output(test_db, project_id=project.id, automatic=True)
    second = await assemble_book_output(test_db, project_id=project.id, automatic=True)
    assert first.output_version_id == second.output_version_id
    await _automatic(test_db, project)
    assert len(calls) == 1  # Policy cleanup never invalidates paid generation checkpoints.
    latest = await load_latest_book_output_version(test_db, output_id=output.id)
    assert latest.manuscript_json['chapters'][0]['pull_quotes'][0]['text'] == (opening if manual else ending)
    assert (latest.id == before_id) is manual
    after_export = await book_outputs.prepare_book_output_export(test_db, user_id=project.user_id,
        project_id=project.id, export_format=book_outputs.MemoirBookOutputExportFormat.PDF)
    assert (before_export.cache_key == after_export.cache_key) is manual
    assert output.status == ('accepted' if manual else 'draft')


@pytest.mark.parametrize("during_generation", [False, True])
async def test_explicit_same_value_chapter_save_claims_copy_without_a_new_version(client, test_db, monkeypatch, during_generation):
    project, chapters, _ = await _output(client, test_db)
    async def generate(**kwargs):
        return _copy(kwargs['snapshot'])
    monkeypatch.setattr(book_outputs, 'generate_manuscript_editorial_copy', generate)
    await _automatic(test_db, project)
    async def claim():
        saved = await book_outputs.update_book_output_chapter_editorial_copy(test_db, user_id=project.user_id,
            project_id=project.id, chapter_id=chapters[0].id,
            req=MemoirBookOutputChapterEditorialCopyUpdateRequest(intro='自动导语1', pull_quote=''))
        assert saved.latest_version.id == before.latest_version.id
        return saved
    before = await book_outputs.update_book_output(test_db, user_id=project.user_id, project_id=project.id,
        req=MemoirBookOutputUpdateRequest(title='新的回忆录标题'))
    if not during_generation:
        await claim()
    async def regenerate(**kwargs):
        if during_generation:
            await claim()
        return _copy(kwargs['snapshot'], 2)
    monkeypatch.setattr(book_outputs, 'generate_manuscript_editorial_copy', regenerate)
    result = await _automatic(test_db, project)
    assert next(s.content for s in result.latest_version.manuscript.front_matter if s.kind == 'preface') == ('自动前言1' if during_generation else '自动前言2')
    assert result.latest_version.manuscript.chapters[0].intro == '自动导语1'
    output = await book_outputs._load_output(test_db, project_id=project.id)
    state = output.metadata_json['automatic_editorial_copy']
    assert state['ownership_version'] == 1
    assert state['manual_targets'] == []
    assert state['manual_chapter_fields'][chapters[0].id] == ['intro', 'pull_quotes']


def test_legacy_copy_ownership_is_migrated_once_and_then_ignores_version_authors():
    from types import SimpleNamespace
    from app.services.memoir.book_output_versions import automatic_editorial_copy_state
    output = SimpleNamespace(metadata_json={})
    before = SimpleNamespace(version=1, created_by='system', manuscript_json={})
    after = SimpleNamespace(version=2, created_by='user', manuscript_json={
        'chapters': [{'chapter_id': 'c', 'intro': '人工导语', 'pull_quotes': []}]})
    state = automatic_editorial_copy_state(output, [before, after])
    assert state.manual_chapter_fields['c'] == ['intro', 'pull_quotes']
    assert output.metadata_json['automatic_editorial_copy']['ownership_version'] == 1
    after.manuscript_json['chapters'].append({'chapter_id': 'new', 'intro': '自动文案'})
    assert automatic_editorial_copy_state(output, [before, after]) == state


async def test_legacy_inline_copy_failure_becomes_a_retryable_job_without_an_ai_retry(client, test_db, monkeypatch):
    from app.services.memoir import automatic_book_draft, book_generation_jobs
    project, _, _ = await _output(client, test_db)
    calls = []
    async def unavailable(**kwargs):
        calls.append(kwargs)
        return None
    monkeypatch.setattr(book_outputs, 'generate_manuscript_editorial_copy', unavailable)
    await _automatic(test_db, project)
    parent = await automatic_book_draft.schedule_book_draft(test_db, project_id=project.id)
    parent.status = 'succeeded'
    parent.result_json = {'editorial_copy_status': 'failed'}
    await test_db.commit()
    await book_generation_jobs.reconcile_legacy_editorial_copy_jobs(test_db)
    copy_job = await test_db.scalar(select(MemoirEditingJob).where(
        MemoirEditingJob.dedupe_key == f'automatic-editorial-copy:{project.id}'))
    from app.services.memoir.editing_job_handlers import register_memoir_editing_job_handlers
    register_memoir_editing_job_handlers()
    assert not await run_test_editing_job(test_db, copy_job.id)
    assert copy_job.status == 'failed' and len(calls) == 1
    await book_generation_jobs.reconcile_legacy_editorial_copy_jobs(test_db)
    await test_db.refresh(copy_job)
    assert copy_job.status == 'failed' and len(calls) == 1


@pytest.mark.parametrize('first_unavailable', [False, True])
async def test_copy_input_arriving_during_generation_remains_runnable(client, test_db, monkeypatch, first_unavailable):
    from app.services.memoir import book_generation_jobs
    from app.services.memoir.editing_job_handlers import register_memoir_editing_job_handlers
    project, _, _ = await _output(client, test_db)
    project_id, user_id = project.id, project.user_id
    req = MemoirBookOutputEditorialCopyRegenerateRequest()
    await book_generation_jobs.schedule_book_editorial_copy_generation(test_db,
        project_id=project_id, user_id=user_id, req=req, automatic=True)
    copy_job = await test_db.scalar(select(MemoirEditingJob).where(
        MemoirEditingJob.dedupe_key == f'automatic-editorial-copy:{project_id}'))
    copy_id = copy_job.id
    calls = []
    async def generate(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            await book_outputs.update_book_output(test_db, project_id=project_id, user_id=user_id,
                req=MemoirBookOutputUpdateRequest(title='生成期间确认的新书名'))
            await book_generation_jobs.schedule_book_editorial_copy_generation(test_db,
                project_id=project_id, user_id=user_id, req=req, automatic=True)
            return None if first_unavailable else _copy(kwargs['snapshot'])
        return _copy(kwargs['snapshot'], 2)
    monkeypatch.setattr(book_outputs, 'generate_manuscript_editorial_copy', generate)
    register_memoir_editing_job_handlers()
    assert not await run_test_editing_job(test_db, copy_id)
    await test_db.refresh(copy_job)
    assert copy_job.status == 'pending'
    assert await run_test_editing_job(test_db, copy_id)
    assert len(calls) == 2 and calls[1]['title'] == '生成期间确认的新书名'


@pytest.mark.parametrize('clear_field', [None, 'preface', 'afterword', 'back_cover_quote'])
async def test_legacy_metadata_form_claims_only_changed_copy(client, test_db, monkeypatch, clear_field):
    from app.services.memoir.book_output_manuscript import stored_manuscript_metadata
    project, _, _ = await _output(client, test_db)
    calls = []
    async def generate(**kwargs):
        calls.append(kwargs)
        return replace(_copy(kwargs['snapshot'], len(calls)), back_cover_quote='自动封底')
    monkeypatch.setattr(book_outputs, 'generate_manuscript_editorial_copy', generate)
    before = await _automatic(test_db, project)
    metadata = stored_manuscript_metadata(before.latest_version.manuscript.model_dump())
    # Old clients submit the whole form, including untouched copy and whitespace.
    payload = {key: f'  {value}  ' for key, value in metadata.items()}
    if clear_field:
        payload[clear_field] = ''
    await book_outputs.update_book_output(test_db, user_id=project.user_id, project_id=project.id,
        req=MemoirBookOutputUpdateRequest(title='只改书名的新标题', **payload))
    output = await book_outputs._load_output(test_db, project_id=project.id)
    assert output.metadata_json['automatic_editorial_copy']['manual_targets'] == ([clear_field] if clear_field else [])
    result = await _automatic(test_db, project)
    actual = stored_manuscript_metadata(result.latest_version.manuscript.model_dump())
    if clear_field:
        assert actual[clear_field] == ''
        assert clear_field not in calls[-1]['targets']
        requested = await book_outputs.regenerate_book_output_editorial_copy(test_db,
            project_id=project.id, user_id=project.user_id,
            req=MemoirBookOutputEditorialCopyRegenerateRequest(targets=['chapter_intros']))
        assert stored_manuscript_metadata(requested.latest_version.manuscript.model_dump())[clear_field] == ''

        from app.models.memoir import MemoirBookChapterDraftVersion
        from app.services.memoir.narrative.book_output_assembly import assemble_book_output
        draft = await test_db.scalar(select(MemoirBookChapterDraftVersion).where(
            MemoirBookChapterDraftVersion.project_id == project.id).limit(1))
        draft.content += '后来全家又搬了一次家。'
        await test_db.commit()
        await assemble_book_output(test_db, project_id=project.id, automatic=True)
        assembled = await book_outputs.get_book_output(test_db, project_id=project.id, user_id=project.user_id)
        assert stored_manuscript_metadata(assembled.latest_version.manuscript.model_dump())[clear_field] == ''

    else:
        assert actual['preface'] == '自动前言2'
        assert actual['afterword'] == '自动后记2'
        assert {'preface', 'afterword', 'back_cover_quote'} <= set(calls[-1]['targets'])


@pytest.mark.parametrize('level,body_changed,manual', [
    ('low', False, False), ('low', True, False), ('medium', False, False),
    ('high', True, False), ('medium', True, True), ('high', False, True),
])
async def test_old_original_quote_reconciles_with_current_body_and_policy(client, test_db, monkeypatch, level, body_changed, manual):
    from app.models.memoir import MemoirBookChapterDraftVersion
    from app.services.memoir.narrative.book_output_assembly import assemble_book_output
    from app.services.memoir.book_output_versions import load_latest_book_output_version
    from tests.test_memoir_book_outputs import _add_output_source_link
    project_id = await ensure_mobile_memoir_project(client)
    project = await test_db.get(Project, project_id)
    project.metadata_json = {**project.metadata_json, 'transcript_polish_level': level}
    plan, chapters = await _create_confirmed_book_plan(test_db, project_id=project_id)
    old_quote = '我十五岁那年发过洪水。'
    ending = '从此我一直记得全家一起走过那条山路的日子。'
    await _add_output_source_link(test_db, project_id=project_id, plan_id=plan.id,
        chapter_id=chapters[0].id, utterance_id='old-flood-source', quote=old_quote)
    draft = await test_db.scalar(select(MemoirBookChapterDraftVersion).where(
        MemoirBookChapterDraftVersion.chapter_id == chapters[0].id))
    draft.content = old_quote + ending
    sections = copy.deepcopy(draft.section_snapshot_json)
    sections[0].update(content=draft.content, used_material_ids=['old-flood-source'])
    draft.section_snapshot_json = sections
    await test_db.commit()
    await assemble_book_output(test_db, project_id=project_id, automatic=True)
    calls = []
    async def generate(**kwargs):
        calls.append(kwargs)
        return _copy(kwargs['snapshot'])
    monkeypatch.setattr(book_outputs, 'generate_manuscript_editorial_copy', generate)
    await _automatic(test_db, project)
    output = await test_db.scalar(select(MemoirBookOutput).where(MemoirBookOutput.project_id == project_id))
    previous = await load_latest_book_output_version(test_db, output_id=output.id)
    manuscript = copy.deepcopy(previous.manuscript_json)
    manuscript['chapters'][0]['pull_quotes'] = [
        {'text': old_quote, 'source': 'original_quote', 'source_ref_ids': ['old-flood-source']}]
    previous.manuscript_json = manuscript
    if manual:
        metadata = copy.deepcopy(output.metadata_json)
        metadata['automatic_editorial_copy']['manual_chapter_fields'][chapters[0].id] = ['pull_quotes']
        output.metadata_json = metadata
    if body_changed:
        draft.content = draft.content.replace('十五岁', '五岁')
        draft.section_snapshot_json = [{**sections[0], 'content': draft.content}]
    await test_db.commit()
    first = await assemble_book_output(test_db, project_id=project_id, automatic=True)
    second = await assemble_book_output(test_db, project_id=project_id, automatic=True)
    assert first.output_version_id == second.output_version_id
    latest = await load_latest_book_output_version(test_db, output_id=output.id)
    quote = latest.manuscript_json['chapters'][0]['pull_quotes'][0]
    retained = manual or (level == 'low' and not body_changed)
    assert quote['text'] == (old_quote if retained else ending)
    assert quote['source'] == ('original_quote' if retained else 'editorial_generated')
    assert previous.manuscript_json['chapters'][0]['pull_quotes'][0]['text'] == old_quote
    assert len(calls) == 1  # Policy repair itself never calls AI.
    if not body_changed:
        await _automatic(test_db, project)
        assert len(calls) == 1  # The evidence-v2 checkpoint is still reusable.


@pytest.mark.parametrize("automatic", [False, True])
async def test_explicit_empty_back_cover_is_saved_and_manual_choice_is_retained(client, test_db, monkeypatch, automatic):
    from app.services.memoir.book_manuscript_ai import _copy_from_payload
    project, chapters, before = await _output(client, test_db)
    stored = copy.deepcopy(before.manuscript_json)
    stored["render_settings"]["back_cover_quote"] = "脱离上下文的旧摘句"
    before.manuscript_json = stored
    await test_db.commit()
    async def generate(**kwargs):
        return _copy_from_payload({"back_cover_quote": ""}, snapshot=kwargs["snapshot"], prompt="", model="test")
    monkeypatch.setattr(book_outputs, "generate_manuscript_editorial_copy", generate)
    result = await book_outputs.regenerate_book_output_editorial_copy(test_db,
        user_id=project.user_id, project_id=project.id,
        req=MemoirBookOutputEditorialCopyRegenerateRequest(targets=["back_cover_quote"]), automatic=automatic)
    assert result.latest_version.manuscript.render_settings.back_cover_quote == ""
    if not automatic:
        output = await book_outputs._load_output(test_db, project_id=project.id)
        assert "back_cover_quote" in output.metadata_json["automatic_editorial_copy"]["manual_targets"]
    updated = await book_outputs.update_book_output_chapter_editorial_copy(test_db,
        user_id=project.user_id, project_id=project.id, chapter_id=chapters[0].id,
        req=MemoirBookOutputChapterEditorialCopyUpdateRequest(intro="后来补写的导语"))
    assert updated.latest_version.manuscript.render_settings.back_cover_quote == ""



async def test_automatic_empty_chapter_quote_survives_another_chapter_reassembly(client, test_db, monkeypatch):
    from app.models.memoir import MemoirBookChapterDraftVersion
    from app.services.memoir.narrative.book_output_assembly import assemble_book_output
    from app.services.memoir.book_output_versions import load_latest_book_output_version
    project, chapters, _ = await _output(client, test_db)
    async def generate(**kwargs):
        return replace(_copy(kwargs["snapshot"]), chapter_intros=[GeneratedChapterIntro(
            chapter_id=item["chapter_id"], intro="车间往事", pull_quote="", valid_empty_quote=True)
            for item in kwargs["snapshot"]])
    monkeypatch.setattr(book_outputs, "generate_manuscript_editorial_copy", generate)
    await _automatic(test_db, project)
    draft = await test_db.scalar(select(MemoirBookChapterDraftVersion).where(
        MemoirBookChapterDraftVersion.chapter_id == chapters[1].id))
    draft.content += "后来我在学校交了新朋友。"
    sections = copy.deepcopy(draft.section_snapshot_json)
    sections[0]["content"] = draft.content
    draft.section_snapshot_json = sections
    await test_db.commit()
    result = await assemble_book_output(test_db, project_id=project.id, automatic=True)
    latest = await load_latest_book_output_version(test_db, output_id=result.output_id)
    assert latest.manuscript_json["chapters"][0]["pull_quotes"] == []
    repeated = await assemble_book_output(test_db, project_id=project.id, automatic=True)
    latest = await load_latest_book_output_version(test_db, output_id=repeated.output_id)
    assert latest.manuscript_json["chapters"][0]["pull_quotes"] == []


async def test_manual_chapter_save_updates_readable_book_without_requesting_models(client, test_db, monkeypatch):
    from tests.test_memoir_continuous_book import _ready_book
    from app.services.memoir import chapter_generation

    project_id, chapter_id, section_id, fake, parent = await _ready_book(client, test_db, monkeypatch)
    before = await test_db.get(MemoirBookOutputVersion, parent.result_json["output_version_id"])
    draft = await chapter_generation.latest_chapter_draft_version(test_db, project_id=project_id, chapter_id=chapter_id)
    calls = len(fake.prompts)
    body = draft.section_snapshot_json[0]["content"] + "。"
    response = await client.post(f"/api/mobile/memoir/projects/{project_id}/book-plan/chapters/{chapter_id}/draft/revise",
        json={"expected_version_id": draft.id, "sections": [{"section_id": section_id, "content": body}]})
    assert response.status_code == 200, response.text
    assert await run_test_editing_job(test_db, parent.id)
    await test_db.refresh(parent)
    latest = await test_db.get(MemoirBookOutputVersion, parent.result_json["output_version_id"])
    assert body in latest.content and latest.id != before.id
    assert latest.manuscript_json["front_matter"] == before.manuscript_json["front_matter"]
    assert latest.manuscript_json["chapters"][0]["intro"] == before.manuscript_json["chapters"][0]["intro"]
    assert len(fake.prompts) == calls and not parent.result_json["review_job_id"]
    assert not await test_db.scalar(select(MemoirEditingJob.id).where(MemoirEditingJob.project_id == project_id,
        MemoirEditingJob.status.in_(["running", "pending"])))


def test_body_excerpt_typo_keeps_its_section_and_does_not_rewrite_authored_copy():
    from app.services.memoir.book_output_versions import retained_chapter_quotes

    former = "那天我们全家一起坐客车回到了家乡。"
    revised = former.replace("客车", "汽车")
    lead = "小时候我们常常在外面过年。"
    stored = {"sections": [{"section_id": "s1", "content": former}],
        "pull_quotes": [{"source": "editorial_generated", "text": former}, {"source": "manual", "text": "家人的题词"}]}
    snapshot = {"sections": [{"section_id": "s1", "content": revised}], "content": lead + revised}
    assert [quote.text for quote in retained_chapter_quotes(stored, snapshot)] == [revised, "家人的题词"]
    snapshot["sections"][0]["section_id"] = "different"
    assert [quote.text for quote in retained_chapter_quotes(stored, snapshot)] == ["家人的题词"]

@pytest.mark.parametrize("automatic", [False, True])
async def test_generate_subtitle_for_saved_book(client, test_db, monkeypatch, automatic):
    project, _, _ = await _output(client, test_db)

    async def generate(**kwargs):
        assert "cover_subtitle" in kwargs["targets"]
        return replace(_copy(kwargs["snapshot"]), cover_subtitle="老街的桂花香")

    monkeypatch.setattr(book_outputs, "generate_manuscript_editorial_copy", generate)
    result = await book_outputs.regenerate_book_output_editorial_copy(test_db, user_id=project.user_id,
        project_id=project.id, req=MemoirBookOutputEditorialCopyRegenerateRequest(targets=[] if automatic else ["subtitle"]),
        automatic=automatic)
    manuscript = result.latest_version.manuscript
    assert manuscript.profile.subtitle == "老街的桂花香"
    assert all("老街的桂花香" in item.content for item in manuscript.front_matter if item.kind in ("cover", "title_page"))
