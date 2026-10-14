import copy
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
from app.services.memoir import book_outputs
from app.services.memoir.book_manuscript_ai import GeneratedChapterIntro, GeneratedManuscriptCopy
from tests.memoir_helpers import ensure_mobile_memoir_project, run_test_editing_job
from tests.test_memoir_book_outputs import _create_confirmed_book_plan, _seed_confirmed_book_output
from tests.test_memoir_automatic_book_draft import _setup


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


@pytest.mark.parametrize('explicit_retry', [False, True])
async def test_book_pipeline_generates_editorial_copy_before_its_review(client, test_db, monkeypatch, explicit_retry):
    from app.services.memoir import automatic_book_draft, book_review_jobs
    project_id, chapter_id, _, _, parent_id = await _setup(client, test_db, monkeypatch)
    if explicit_retry:
        await automatic_book_draft.schedule_book_draft(test_db, project_id=project_id, source='retry')
        await test_db.commit()
    calls = []
    retries, reviews = [], []
    regenerate, enqueue_review = book_outputs.regenerate_book_output_editorial_copy, book_review_jobs.enqueue_book_review_job
    async def record_retry(*args, **kwargs):
        retries.append(kwargs.get('retry', False))
        return await regenerate(*args, **kwargs)
    async def record_review(*args, **kwargs):
        reviews.append(kwargs.get('automatic', False))
        return await enqueue_review(*args, **kwargs)
    monkeypatch.setattr(book_outputs, 'regenerate_book_output_editorial_copy', record_retry)
    monkeypatch.setattr(book_review_jobs, 'enqueue_book_review_job', record_review)
    async def generate(**kwargs):
        calls.append(kwargs)
        return _copy(kwargs['snapshot'])
    monkeypatch.setattr(book_outputs, 'generate_manuscript_editorial_copy', generate)
    assert not await run_test_editing_job(test_db, parent_id)
    parent = await test_db.get(MemoirEditingJob, parent_id)
    assert await run_test_editing_job(test_db, parent.result_json['chapters'][chapter_id]['job_id'])
    assert not await run_test_editing_job(test_db, parent_id)
    assert len(calls) == 1
    assert await run_test_editing_job(test_db, parent.result_json['review_job_id'])
    assert await run_test_editing_job(test_db, parent_id)
    assert len(calls) == 1
    assert retries == [explicit_retry, False]
    assert reviews == [not explicit_retry, True]


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
        return _copy(kwargs['snapshot'], len(calls))
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
    await _add_narration(client, test_db, project_id, section_id, fake)
    assert not await run_test_editing_job(test_db, parent_id)
    parent = await test_db.get(MemoirEditingJob, parent_id)
    assert await run_test_editing_job(test_db, parent.result_json['chapters'][chapter_id]['job_id'])
    assert not await run_test_editing_job(test_db, parent_id)
    parent = await test_db.get(MemoirEditingJob, parent_id)
    assert await run_test_editing_job(test_db, parent.result_json['review_job_id'])
    assert await run_test_editing_job(test_db, parent_id)
    progress = await draft_progress.read_draft_progress(test_db, project_id=project_id)
    assert progress.status == 'ready'
    current = await book_outputs.get_book_output(test_db, user_id=user_id, project_id=project_id)
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


@pytest.mark.parametrize('legacy_state', [False, True])
@pytest.mark.parametrize('failed_update', [False, True, 'missing_intro'])
async def test_only_changed_chapter_copy_is_regenerated(client, test_db, monkeypatch, legacy_state, failed_update):
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
            return replace(generated, chapter_intros=[]) if failed_update == 'missing_intro' else None
        return generated
    monkeypatch.setattr(book_outputs, 'generate_manuscript_editorial_copy', generate)
    first = await _automatic(test_db, project)
    output = await test_db.scalar(select(MemoirBookOutput).where(MemoirBookOutput.project_id == project_id))
    if legacy_state:
        metadata = copy.deepcopy(output.metadata_json)
        metadata['automatic_editorial_copy'].pop('chapter_input_signatures')
        metadata['automatic_editorial_copy'].pop('failed_chapter_ids')
        output.metadata_json = metadata
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
    original_draft = parent.result_json['chapters'][chapter_id]['draft_version_id']
    progress = await draft_progress.read_draft_progress(test_db, project_id=project_id)
    assert progress.status == 'ready' and progress.can_retry
    await draft_progress.retry_draft_progress(test_db, project_id=project_id)
    assert not await run_test_editing_job(test_db, parent.id)
    assert await run_test_editing_job(test_db, parent.result_json['review_job_id'])
    assert await run_test_editing_job(test_db, parent.id)
    assert parent.result_json['chapters'][chapter_id]['draft_version_id'] == original_draft
    assert len(calls) == 2
    progress = await draft_progress.read_draft_progress(test_db, project_id=project_id)
    assert progress.status == 'ready' and not progress.can_retry
