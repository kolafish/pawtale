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
    assert manuscript.chapters[1].intro == '自动导语2'
    assert 'preface' not in calls[-1]['targets'] and 'afterword' not in calls[-1]['targets']


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


async def test_book_pipeline_generates_editorial_copy_before_its_review(client, test_db, monkeypatch):
    _, chapter_id, _, _, parent_id = await _setup(client, test_db, monkeypatch)
    calls = []
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
