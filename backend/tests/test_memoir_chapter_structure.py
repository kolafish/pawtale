from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memoir import MemoirSectionMaterial
from app.models.projects import Project
from app.services.memoir import chapter_structure as structure
from app.services.memoir.story_tree_growth import _load_tree, build_story_growth_prompt
from tests.memoir_helpers import create_test_book_plan_with_chapters, create_test_voice_transcript, ensure_mobile_memoir_project, memoir_source_ref


async def _book(client, db):
    pid = await ensure_mobile_memoir_project(client)
    texts = ['妈妈叫何进。', '小时候我在妈妈课堂上读书。', '三岁或者四岁的时候去了学前班。', '后来我在饭馆上班。']
    sources = [await create_test_voice_transcript(client, db, text, project_id=pid) for text in texts]
    plan, chapters = await create_test_book_plan_with_chapters(db, project_id=pid,
        chapter_titles=['妈妈的名字', '妈妈的课堂', '学前班', '工作'],
        source_refs_by_chapter=[[memoir_source_ref(s)] for s in sources])
    plan.created_by = 'ai'
    for chapter in chapters:
        chapter.metadata_json = {'created_from_material_assignment_by': 'ai'}
    for link in (await db.scalars(select(MemoirSectionMaterial).where(MemoirSectionMaterial.project_id == pid))).all():
        link.assigned_by = 'ai'
    await db.commit()
    user_id = await db.scalar(select(Project.user_id).where(Project.id == pid))
    return pid, user_id


def _merge(context, *, merge_sections=False):
    output = context.outline.model_copy(deep=True)
    first, second, third, fourth = output.chapters
    first.title = '妈妈与童年的课堂'
    first.sections += second.sections + third.sections
    if merge_sections:
        first.sections[1].section_refs += first.sections.pop(2).section_refs
        first.sections[1].title = '妈妈课堂与学前班'
    output.chapters = [first, fourth]
    return output


def _provider(monkeypatch, answer, *, during_call=None):
    calls = []
    async def digest(*args, **kwargs):
        return None
    async def summaries(*args, **kwargs):
        return {}, {}
    async def generate(*args, **kwargs):
        calls.append(args[2])
        if during_call:
            await during_call()
        return SimpleNamespace(value=answer(args[2]) if callable(answer) else answer)
    monkeypatch.setattr(structure, 'generate_or_reuse_chapter_digest', digest)
    monkeypatch.setattr(structure, 'load_structure_summaries', summaries)
    monkeypatch.setattr(structure, 'get_ai_service', lambda _: None)
    monkeypatch.setattr(structure, 'generate_structured_with_client', generate)
    return calls


@pytest.mark.parametrize('merge_sections', [False, True])
async def test_review_merges_chapters_preserves_sources_and_does_not_repeat(client, test_db, monkeypatch, merge_sections):
    pid, uid = await _book(client, test_db)
    plan, chapters, sections = await _load_tree(test_db, project_id=pid)
    context = await structure.structure_context(test_db, plan, chapters, sections)
    assert not context.locked_chapters
    original_links = {m.id: (m.utterance_id, m.transcript_version_id, m.include_in_book) for s in sections for m in s.materials}
    kept_ids = {s.id for s in sections}
    output = _merge(context, merge_sections=merge_sections)
    if merge_sections:
        kept_ids.remove(context.section_refs.decode(context.outline.chapters[2].sections[0].section_refs[0]))
    calls = _provider(monkeypatch, output)
    await structure.review_story_structure(test_db, user_id=uid, project_id=pid)
    await structure.review_story_structure(test_db, user_id=uid, project_id=pid)
    plan, chapters, sections = await _load_tree(test_db, project_id=pid)
    assert len(calls) == 1
    assert [c.title for c in chapters] == ['妈妈与童年的课堂', '工作']
    assert {s.id for s in sections} == kept_ids
    assert {m.id: (m.utterance_id, m.transcript_version_id, m.include_in_book) for s in sections for m in s.materials} == original_links
    assert len([s for s in sections if s.chapter_id == chapters[0].id]) == (2 if merge_sections else 3)


async def test_split_and_rename_keep_section_identity_and_only_recheck_changed_content(client, test_db, monkeypatch):
    pid, uid = await _book(client, test_db)
    plan, chapters, sections = await _load_tree(test_db, project_id=pid)
    # Put all source sections under one chapter to exercise moving several intact sections back out.
    for i, section in enumerate(sections):
        section.chapter_id = chapters[0].id
        section.sort_order = i + 10
    await test_db.flush()
    for chapter in chapters[1:]:
        await test_db.refresh(chapter, ['sections', 'draft'])
        await test_db.delete(chapter)
    await test_db.commit()
    plan, chapters, sections = await _load_tree(test_db, project_id=pid)
    context = await structure.structure_context(test_db, plan, chapters, sections)
    output = context.outline.model_copy(deep=True)
    work = output.chapters[0].sections.pop()
    output.chapters.append(structure.ChapterGroup(chapter_ref='N1', title='工作以后', sections=[work]))
    output.chapters[0].title = '童年与妈妈'
    calls = _provider(monkeypatch, output)
    await structure.review_story_structure(test_db, user_id=uid, project_id=pid)
    await structure.review_story_structure(test_db, user_id=uid, project_id=pid)
    assert len(calls) == 1
    plan, chapters, sections = await _load_tree(test_db, project_id=pid)
    assert [c.title for c in chapters] == ['童年与妈妈', '工作以后']
    assert {s.id for s in sections} == {context.section_refs.decode(r) for c in output.chapters for g in c.sections for r in g.section_refs}
    sections[0].title = '新的小节标题'
    sections[0].version += 1
    await test_db.commit()
    fresh = await structure.structure_context(test_db, plan, chapters, sections)
    calls = _provider(monkeypatch, fresh.outline)
    await structure.review_story_structure(test_db, user_id=uid, project_id=pid)
    assert len(calls) == 1


@pytest.mark.parametrize('bad', ['missing', 'duplicate', 'unknown', 'manual_title', 'excluded'])
async def test_invalid_or_protected_structure_never_writes(client, test_db, monkeypatch, bad):
    pid, uid = await _book(client, test_db)
    plan, chapters, sections = await _load_tree(test_db, project_id=pid)
    if bad == 'manual_title':
        chapters[1].metadata_json = {'title_updated_by': 'user'}
    if bad == 'excluded':
        next(s for s in sections if s.chapter_id == chapters[1].id).materials[0].include_in_book = False
    await test_db.commit()
    context = await structure.structure_context(test_db, plan, chapters, sections)
    output = _merge(context)
    if bad == 'missing':
        output.chapters[0].sections.pop()
    if bad == 'duplicate':
        output.chapters[0].sections.append(output.chapters[0].sections[0])
    if bad == 'unknown':
        output.chapters[0].chapter_ref = 'C99'
    _provider(monkeypatch, output)
    with pytest.raises(ValueError):
        await structure.review_story_structure(test_db, user_id=uid, project_id=pid)
    after_plan, after_chapters, after_sections = await _load_tree(test_db, project_id=pid)
    after = await structure.structure_context(test_db, after_plan, after_chapters, after_sections)
    assert after.signature == context.signature
    assert structure.CHECKED_KEY not in after_plan.metadata_json


async def test_user_edit_while_ai_runs_rejects_stale_proposal(client, test_db, monkeypatch):
    pid, uid = await _book(client, test_db)
    plan, chapters, sections = await _load_tree(test_db, project_id=pid)
    context = await structure.structure_context(test_db, plan, chapters, sections)
    async def edit():
        async with AsyncSession(test_db.bind, expire_on_commit=False) as other:
            chapter = await other.get(type(chapters[0]), chapters[0].id)
            chapter.title = '用户刚修改的标题'
            chapter.metadata_json = {'title_updated_by': 'user'}
            await other.commit()
    _provider(monkeypatch, _merge(context), during_call=edit)
    with pytest.raises(HTTPException) as exc:
        await structure.review_story_structure(test_db, user_id=uid, project_id=pid)
    assert exc.value.status_code == 409
    _, after, _ = await _load_tree(test_db, project_id=pid)
    assert len(after) == 4 and after[0].title == '用户刚修改的标题'


async def test_growth_receives_valid_chapter_and_section_summaries(client, test_db, monkeypatch):
    pid, _ = await _book(client, test_db)
    _, chapters, sections = await _load_tree(test_db, project_id=pid)
    async def chapter_digest(db, *, chapter_id, **kwargs):
        return SimpleNamespace(status='ready' if chapter_id == chapters[0].id else 'stale', digest_json={'summary': '妈妈在学校教书。'})
    async def section_digest(db, *, section_id, **kwargs):
        return SimpleNamespace(digest_json={'summary': '小时候跟妈妈去课堂。'}) if section_id == sections[0].id else None
    monkeypatch.setattr(structure, 'get_current_chapter_digest', chapter_digest)
    monkeypatch.setattr(structure, 'get_current_section_digest', section_digest)
    cs, ss = await structure.load_structure_summaries(test_db, project_id=pid, chapters=chapters, sections=sections)
    assert cs == {chapters[0].id: '妈妈在学校教书。'}
    assert ss == {sections[0].id: '小时候跟妈妈去课堂。'}
    prompt, _, _ = build_story_growth_prompt(utterances=[], chapters=chapters, sections=sections,
        interviewer_lines={}, chapter_summaries=cs, section_summaries=ss)
    assert '妈妈在学校教书。' in prompt and '小时候跟妈妈去课堂。' in prompt


async def test_confirmed_answer_follows_sources_after_chapter_is_absorbed(client, test_db, monkeypatch):
    from tests.test_memoir_fact_snapshot_lifecycle import decision
    from app.services.memoir import fact_decisions
    pid, uid = await _book(client, test_db)
    plan, chapters, sections = await _load_tree(test_db, project_id=pid)
    original_chapter = chapters[1].id
    material_id = next(s for s in sections if s.chapter_id == original_chapter).materials[0].utterance_id
    issue = await decision(test_db, pid, original_chapter, material_id, note='妈妈的名字是何进。')
    issue.related_material_ids_json = []  # Legacy answers had only a chapter anchor.
    await test_db.commit()
    context = await structure.structure_context(test_db, plan, chapters, sections)
    _provider(monkeypatch, _merge(context, merge_sections=True))
    await structure.review_story_structure(test_db, user_id=uid, project_id=pid)
    await test_db.refresh(issue)
    assert issue.chapter_id is None and issue.scope == 'book'
    assert issue.related_material_ids_json == [material_id]
    decisions = await fact_decisions.load_fact_decisions(test_db, project_id=pid)
    assert fact_decisions.for_sources(decisions, chapter_id=chapters[0].id, material_ids=[material_id])[0].answer == '妈妈的名字是何进。'


async def test_manual_draft_is_kept_while_other_chapters_merge(client, test_db, monkeypatch):
    from tests.memoir_helpers import create_test_chapter_draft
    from app.models.memoir import MemoirBookChapterDraftVersion
    from app.domain.memoir.types import MemoirBookChapterDraftStatus
    pid, uid = await _book(client, test_db)
    plan, chapters, sections = await _load_tree(test_db, project_id=pid)
    protected = chapters[-1]
    draft = await create_test_chapter_draft(test_db, project_id=pid, chapter_id=protected.id,
        status=MemoirBookChapterDraftStatus.DRAFT, content='我亲手改过的工作回忆。')
    context = await structure.structure_context(test_db, plan, chapters, sections)
    assert protected.id in context.locked_chapters
    _provider(monkeypatch, _merge(context))
    await structure.review_story_structure(test_db, user_id=uid, project_id=pid)
    version = await test_db.scalar(select(MemoirBookChapterDraftVersion).where(MemoirBookChapterDraftVersion.draft_id == draft.id))
    assert version.content == '我亲手改过的工作回忆。'
    assert version.chapter_id == protected.id and version.created_by == 'user'


async def test_merge_failure_rolls_back_the_whole_directory(client, test_db, monkeypatch):
    pid, uid = await _book(client, test_db)
    plan, chapters, sections = await _load_tree(test_db, project_id=pid)
    context = await structure.structure_context(test_db, plan, chapters, sections)
    sources = structure.material_identity(sections)
    _provider(monkeypatch, _merge(context))
    async def fail(*args, **kwargs):
        raise RuntimeError('simulated draft invalidation failure')
    monkeypatch.setattr(structure, 'mark_section_structure_changed', fail)
    with pytest.raises(RuntimeError):
        await structure.review_story_structure(test_db, user_id=uid, project_id=pid)
    await test_db.rollback()  # Existing editing-job transaction boundary.
    plan, chapters, sections = await _load_tree(test_db, project_id=pid)
    assert (await structure.structure_context(test_db, plan, chapters, sections)).outline == context.outline
    assert structure.material_identity(sections) == sources


async def test_structure_runs_after_session_stops_not_each_material(client, test_db, monkeypatch):
    from app.models.memoir import MemoirVoiceSession
    from app.services.memoir import story_tree_growth as growth
    from tests.test_memoir_section_organization import _stopped_growth_job
    pid, _ = await _book(client, test_db)
    plan, chapters, sections = await _load_tree(test_db, project_id=pid)
    context = await structure.structure_context(test_db, plan, chapters, sections)
    calls = _provider(monkeypatch, context.outline)
    source, job = await _stopped_growth_job(client, test_db, pid, '妈妈教我认字，我一直记得。')
    session = await test_db.get(MemoirVoiceSession, source['session']['id'])
    session.status = 'listening'
    await test_db.commit()
    async def assign(**kwargs):
        return [growth.GrowthGroup(utterance_ids=[source['utterance_id']], action='assign_existing', section_id=sections[0].id)]
    monkeypatch.setattr(growth, 'decide_story_growth', assign)
    await growth.run_story_tree_growth_job(test_db, job)
    assert calls == []
    session.status = 'stopped'
    await test_db.commit()
    await growth.run_story_tree_growth_job(test_db, job)
    await growth.run_story_tree_growth_job(test_db, job)
    assert len(calls) == 1


@pytest.mark.db_independent
async def test_manual_and_automatic_directory_writers_share_lock_order(client, test_db):
    import asyncio
    from sqlalchemy import text
    from app.services.memoir.sections import lock_project_chapters
    from app.services.memoir.sections import lock_plan_structure, lock_chapter_structure
    pid, _ = await _book(client, test_db)
    _, chapters, _ = await _load_tree(test_db, project_id=pid)
    await test_db.commit()
    async with AsyncSession(test_db.bind) as automatic, AsyncSession(test_db.bind) as manual:
        await lock_plan_structure(automatic, project_id=pid)
        manual_pid = await manual.scalar(text('SELECT pg_backend_pid()'))
        async def writer():
            await lock_project_chapters(manual, project_id=pid)
            await lock_plan_structure(manual, project_id=pid)
            await manual.commit()
        task = asyncio.create_task(writer())
        try:
            async def wait_until_blocked():
                while not await test_db.scalar(text('SELECT cardinality(pg_blocking_pids(:pid)) > 0'), {'pid': manual_pid}):
                    await asyncio.sleep(0.01)
            await asyncio.wait_for(wait_until_blocked(), timeout=3)
            # The waiting manual writer must not already hold chapter locks.
            await asyncio.wait_for(lock_chapter_structure(automatic, project_id=pid, chapter_id=chapters[0].id), timeout=1)
            await automatic.commit()
            await asyncio.wait_for(task, timeout=3)
        finally:
            await automatic.rollback()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


async def test_growth_reports_final_directory_after_a_new_chapter_is_merged(client, test_db, monkeypatch):
    from app.services.memoir import story_tree_growth as growth
    from tests.test_memoir_section_organization import _stopped_growth_job
    pid, _ = await _book(client, test_db)
    plan, chapters, sections = await _load_tree(test_db, project_id=pid)
    output = (await structure.structure_context(test_db, plan, chapters, sections)).outline
    source, job = await _stopped_growth_job(client, test_db, pid, '后来又跟妈妈去课堂，老师给我垫了板凳。')
    async def assign(**kwargs):
        return [growth.GrowthGroup(utterance_ids=[source['utterance_id']], action='create_chapter',
            new_chapter_title='课堂的板凳', new_section_title='老师垫板凳')]
    async def absorb():
        plan, chapters, sections = await _load_tree(test_db, project_id=pid)
        current = (await structure.structure_context(test_db, plan, chapters, sections)).outline
        temporary = current.chapters.pop()
        current.chapters[1].sections.extend(temporary.sections)
        output.chapters = current.chapters
    _provider(monkeypatch, output, during_call=absorb)
    monkeypatch.setattr(growth, 'decide_story_growth', assign)
    result = await growth.run_story_tree_growth_job(test_db, job)
    assert result.result_json['new_chapter_ids'] == []
    assert len(result.result_json['new_section_ids']) == 1
    _, chapters, sections = await _load_tree(test_db, project_id=pid)
    assert len(chapters) == 4
    target = next(s for s in sections if any(m.utterance_id == source['utterance_id'] for m in s.materials))
    assert target.chapter_id == chapters[1].id


async def _book_review_feedback(db, pid, *, signature='review-input'):
    from app.models.memoir import MemoirBookReview
    from app.services.memoir import book_reviews
    plan, chapters, _ = await _load_tree(db, project_id=pid)
    context = book_reviews.BookReviewContext(
        project_id=pid, plan_id=plan.id, book_metadata={}, input_signature=signature,
        chapters=tuple(book_reviews.BookReviewChapter(
            chapter_id=c.id, title=c.title, sort_order=c.sort_order, digest_id='digest',
            digest_signature='digest-input', digest={}, draft_version_id='draft', draft_hash='text',
            material_ids=frozenset(),
        ) for c in chapters),
    )
    normalized = book_reviews.normalize_book_review({
        'summary': '童年故事被分成多个窄章节。',
        'structure_findings': [{'kind': 'chapter_structure', 'chapter_ids': ['C1', 'C2'],
                                'reason': 'C1 与 C2 同属童年家庭主题，建议合章后按经历保留小节。'}],
    }, context=context)
    review = MemoirBookReview(project_id=pid, input_signature=signature,
        engine=book_reviews.BOOK_REVIEW_ENGINE, status='ready', review_json=normalized)
    db.add(review)
    await db.commit()
    return review


async def test_review_structure_findings_are_visible_without_creating_narration_questions(client, test_db):
    pid, _ = await _book(client, test_db)
    review = await _book_review_feedback(test_db, pid)
    _, chapters, _ = await _load_tree(test_db, project_id=pid)
    assert review.review_json['structure_findings'][0]['chapter_ids'] == [c.id for c in chapters[:2]]
    assert chapters[0].title in review.review_json['structure_guidance'][0]
    assert 'C1' not in review.review_json['structure_guidance'][0]
    assert review.review_json['candidate_issues'] == []
    assert review.review_json['editorial_actions'] == []


async def test_review_feedback_uses_the_shared_writer_once_per_source_input(client, test_db, monkeypatch):
    from app.services.memoir import book_reviews, sections as section_service
    from app.domain.memoir.types import MemoirSectionMaterialAssignedBy
    pid, uid = await _book(client, test_db)
    plan, chapters, sections = await _load_tree(test_db, project_id=pid)
    context = await structure.structure_context(test_db, plan, chapters, sections)
    # The narration's ordinary structure check has already completed.
    plan.metadata_json = {structure.CHECKED_KEY: context.signature}
    await test_db.commit()
    review = await _book_review_feedback(test_db, pid)
    async def current(*args, **kwargs):
        return review
    monkeypatch.setattr(book_reviews, 'get_current_book_review', current)
    calls = _provider(monkeypatch, _merge(context, merge_sections=True))
    original = structure.material_identity(sections)
    assert await structure.review_story_structure(test_db, user_id=uid, project_id=pid, book_review=review)
    assert '全书审阅发现的目录问题' in calls[0] and '童年家庭主题' in calls[0]
    _, chapters, sections = await _load_tree(test_db, project_id=pid)
    assert structure.material_identity(sections) == original
    # New wording/review IDs or AI-generated directory names must not loop.
    review = await _book_review_feedback(test_db, pid, signature='regenerated-review')
    assert not await structure.review_story_structure(test_db, user_id=uid, project_id=pid, book_review=review)
    assert len(calls) == 1
    source = await create_test_voice_transcript(client, test_db, '妈妈后来又教我写字。', project_id=pid)
    _, changed_ids = await section_service.assign_material_to_section(test_db, project_id=pid, section_id=sections[0].id,
        utterance_id=source['utterance_id'], assigned_by=MemoirSectionMaterialAssignedBy.AI, assignment_reason='new_story')
    await section_service.finalize_section_changes(test_db, project_id=pid, section_ids=changed_ids)
    await test_db.commit()
    plan, chapters, sections = await _load_tree(test_db, project_id=pid)
    new_context = await structure.structure_context(test_db, plan, chapters, sections)
    calls = _provider(monkeypatch, new_context.outline)
    assert not await structure.review_story_structure(test_db, user_id=uid, project_id=pid, book_review=review)
    assert len(calls) == 1  # A no-change decision for genuinely changed source input.


async def test_outdated_book_review_cannot_apply_a_structure_proposal(client, test_db, monkeypatch):
    from app.services.memoir import book_reviews
    pid, uid = await _book(client, test_db)
    plan, chapters, sections = await _load_tree(test_db, project_id=pid)
    context = await structure.structure_context(test_db, plan, chapters, sections)
    review = await _book_review_feedback(test_db, pid)
    async def current(*args, **kwargs):
        return review
    async def invalidate():
        async def stale(*args, **kwargs):
            return None
        monkeypatch.setattr(book_reviews, 'get_current_book_review', stale)
    monkeypatch.setattr(book_reviews, 'get_current_book_review', current)
    _provider(monkeypatch, _merge(context), during_call=invalidate)
    with pytest.raises(HTTPException) as exc:
        await structure.review_story_structure(test_db, user_id=uid, project_id=pid, book_review=review)
    assert exc.value.status_code == 409
    plan, chapters, sections = await _load_tree(test_db, project_id=pid)
    assert len(chapters) == 4
    assert structure.FEEDBACK_CHECKED_KEY not in plan.metadata_json


@pytest.mark.parametrize('rebuild_digests', [False, True])
@pytest.mark.parametrize('operation', ['keep', 'merge', 'split'])
async def test_growth_worker_generates_real_digests_and_schedules_book(client, test_db, monkeypatch, rebuild_digests, operation):
    from app.clock import utcnow_naive
    from app.domain.memoir.types import MemoirEditingJobType
    from app.models.memoir import MemoirAutomationEvent, MemoirEditingJob, MemoirVoiceSession
    from app.services.memoir import chapter_digests, editing_jobs, section_digests, story_tree_growth as growth
    from tests.test_memoir_chapter_pipeline import FakeChapterAI
    from tests.test_memoir_section_organization import _stopped_growth_job
    pid, _ = await _book(client, test_db)
    plan, chapters, sections = await _load_tree(test_db, project_id=pid)
    context = await structure.structure_context(test_db, plan, chapters, sections)
    original_chapter_ids = [c.id for c in chapters]
    original_section_ids = {s.id for s in sections}
    original_sources = structure.material_identity(sections)
    target_section_id = sections[0].id
    real_digest, real_summaries = structure.generate_or_reuse_chapter_digest, structure.load_structure_summaries
    merge_sections = operation == 'merge'
    split_section = operation == 'split'
    def split_answer(prompt):
        import json
        payload, _ = json.JSONDecoder().raw_decode(prompt.split('当前目录：\n', 1)[1])
        target_ref = context.section_refs.encode(target_section_id)
        current = next(s for c in payload['chapters'] for s in c['sections'] if s['section_refs'] == [target_ref])
        answer = context.outline.model_dump()
        target_chapter = next(c for c in answer['chapters'] if any(s['section_refs'] == [target_ref] for s in c['sections']))
        target_chapter['sections'].append({'section_refs': ['T1'], 'title': '课堂上的板凳'})
        answer['section_splits'] = [{'source_section_ref': target_ref, 'new_section_ref': 'T1',
            'material_refs': [current['materials'][-1]['material_ref']]}]
        return structure.StructureOutput.model_validate(answer)
    calls = _provider(monkeypatch, split_answer if split_section else
        _merge(context, merge_sections=True) if merge_sections else context.outline)
    monkeypatch.setattr(structure, 'generate_or_reuse_chapter_digest', real_digest)
    monkeypatch.setattr(structure, 'load_structure_summaries', real_summaries)
    fake = FakeChapterAI(section_material_id='', chapter_section_id='', chapter_material_id='', draft_material_ids=[])
    monkeypatch.setattr(chapter_digests, 'get_ai_service', lambda _: fake)
    monkeypatch.setattr(section_digests, 'get_ai_service', lambda _: fake)
    if rebuild_digests:
        for chapter_id in original_chapter_ids:
            await real_digest(test_db, project_id=pid, chapter_id=chapter_id, ai_client=fake)
    prompts_before = len(fake.prompts)
    source, job = await _stopped_growth_job(client, test_db, pid, '小时候跟妈妈去课堂，她给我垫了板凳。')
    session_id, job_id = source['session']['id'], job.id
    job.status = 'pending'
    job.run_after = utcnow_naive()
    job.payload_json = {**job.payload_json, 'source': 'retry' if rebuild_digests else 'transcript_ready'}
    await test_db.commit()
    async def assign(**kwargs):
        return [growth.GrowthGroup(utterance_ids=[source['utterance_id']], action='assign_existing', section_id=target_section_id)]
    monkeypatch.setattr(growth, 'decide_story_growth', assign)
    monkeypatch.setitem(editing_jobs._HANDLERS, MemoirEditingJobType.ORGANIZE_MATERIAL, growth.run_story_tree_growth_job)
    lease, = await editing_jobs.claim_due_editing_jobs(test_db, project_id=pid, job_type=MemoirEditingJobType.ORGANIZE_MATERIAL, limit=1)
    assert lease.job_id == job_id
    assert await editing_jobs.run_editing_job(test_db, lease)
    await test_db.refresh(job)
    session = await test_db.get(MemoirVoiceSession, session_id, populate_existing=True)
    assert job.status == 'succeeded' and not job.error_message and session.status == 'stopped'
    assert job.result_json['voice_session_id'] == session_id
    assert job.result_json['organized_utterance_ids'] == [source['utterance_id']]
    assert job.result_json['new_chapter_ids'] == []
    assert len(job.result_json['new_section_ids']) == int(split_section)
    assert job.result_json['growth']['placed_ids'] == []
    assert len(fake.prompts) - prompts_before == (2 if rebuild_digests else 8)
    assert len(calls) == 1
    assert '朋友发来山桃花照片' in calls[0]  # The real section digest reached the directory prompt.
    _, chapters, sections = await _load_tree(test_db, project_id=pid)
    assert len(chapters) == (2 if merge_sections else 4)
    assert len(sections) == (3 if merge_sections else 5 if split_section else 4)
    assert {c.id for c in chapters} <= set(original_chapter_ids)
    assert {s.id for s in sections} - original_section_ids == set(job.result_json['new_section_ids'])
    assert set(original_sources) < set(structure.material_identity(sections))
    event = await test_db.scalar(select(MemoirAutomationEvent).where(MemoirAutomationEvent.editing_job_id == job_id,
        MemoirAutomationEvent.event_type == 'story_structure_updated'))
    assert event.utterance_ids_json == [source['utterance_id']]
    assert event.metadata_json['voice_session_id'] == session_id
    book_job = await test_db.scalar(select(MemoirEditingJob).where(MemoirEditingJob.dedupe_key == f'update-book-draft:{pid}'))
    assert book_job.status == 'pending'
    assert book_job.payload_json['source'] == ('retry' if rebuild_digests else 'organized')
    assert book_job.run_after <= utcnow_naive()


async def test_adopted_section_rewrite_keeps_manual_text_and_history_during_structure_review(client, test_db, monkeypatch):
    from app.models.memoir import MemoirBookChapterDraftVersion
    from app.domain.memoir.types import MemoirBookChapterDraftStatus
    from app.services.memoir import chapter_generation as cg
    from tests.test_memoir_book_completion import _create_current_draft
    pid, uid = await _book(client, test_db)
    _, chapters, _ = await _load_tree(test_db, project_id=pid)
    chapter_id = chapters[-1].id
    _, latest = await _create_current_draft(test_db, project_id=pid, chapter_id=chapter_id,
        status=MemoirBookChapterDraftStatus.DRAFT)
    ctx = await cg.load_chapter_generation_context(test_db, project_id=pid, chapter_id=chapter_id)
    _, adopted = await cg.apply_chapter_section_candidate(test_db, project_id=pid, chapter_id=chapter_id,
        section_id=ctx.sections[0].section_id, expected_version_id=latest.id, expected_input_signature=ctx.input_signature,
        candidate_title=ctx.sections[0].title, candidate_content='这是我采纳修改后的工作往事。', feedback='调整措辞',
        requested_ai_model=None, resolved_ai_model=None)
    await test_db.commit()
    assert adopted.created_by == 'ai' and adopted.section_snapshot_json[0]['user_edited']
    plan, chapters, sections = await _load_tree(test_db, project_id=pid)
    context = await structure.structure_context(test_db, plan, chapters, sections)
    assert context.locked_chapters == {chapter_id}
    invalid = _merge(context)
    invalid.chapters[0].sections.extend(invalid.chapters.pop().sections)
    with pytest.raises(ValueError):
        structure.validate_structure(invalid, context)
    _provider(monkeypatch, _merge(context))
    await structure.review_story_structure(test_db, user_id=uid, project_id=pid)
    await test_db.refresh(adopted)
    assert adopted.content == '这是我采纳修改后的工作往事。'
    history = (await test_db.scalars(select(MemoirBookChapterDraftVersion).where(
        MemoirBookChapterDraftVersion.chapter_id == chapter_id).order_by(MemoirBookChapterDraftVersion.version))).all()
    assert [v.id for v in history] == [latest.id, adopted.id]


async def test_actual_manual_move_keeps_order_independently_of_title_ownership(client, test_db, monkeypatch):
    pid, uid = await _book(client, test_db)
    _, chapters, _ = await _load_tree(test_db, project_id=pid)
    moved_id = chapters[-1].id
    response = await client.post(f'/api/mobile/memoir/projects/{pid}/book-plan/structure-commands',
        json={'type': 'move_chapter', 'chapter_id': moved_id, 'direction': 'up'})
    assert response.status_code == 200
    # An unrelated later operation must not erase the manual ordering constraint.
    response = await client.post(f'/api/mobile/memoir/projects/{pid}/book-plan/structure-commands',
        json={'type': 'rename_chapter', 'chapter_id': moved_id, 'title': '工作以后'})
    assert response.status_code == 200
    plan, chapters, sections = await _load_tree(test_db, project_id=pid)
    context = await structure.structure_context(test_db, plan, chapters, sections)
    assert context.manual_chapter_order == tuple(c.id for c in chapters)
    assert not context.locked_chapters
    output = context.outline.model_copy(deep=True)
    output.chapters[0], output.chapters[1] = output.chapters[1], output.chapters[0]
    _provider(monkeypatch, output)
    with pytest.raises(ValueError, match='人工调整'):
        await structure.review_story_structure(test_db, user_id=uid, project_id=pid)
    # Chapter identities and relative order stay fixed; automatic titles can still improve.
    output = context.outline.model_copy(deep=True)
    output.chapters[0].title = '妈妈与家'
    _provider(monkeypatch, output)
    await structure.review_story_structure(test_db, user_id=uid, project_id=pid)
    _, after, _ = await _load_tree(test_db, project_id=pid)
    assert [c.id for c in after] == list(context.manual_chapter_order)
    assert after[0].title == '妈妈与家'


async def test_completion_session_started_during_review_preserves_issue_and_target(client, test_db, monkeypatch):
    from app.models.memoir import MemoirCompletionIssue, MemoirVoiceSession
    from tests.test_memoir_completion_issues_v2 import _completion_issue
    from tests.memoir_helpers import post_mobile_memoir_voice_session
    pid, uid = await _book(client, test_db)
    plan, chapters, sections = await _load_tree(test_db, project_id=pid)
    target = next(s for s in sections if s.chapter_id == chapters[1].id)
    issue = await _completion_issue(test_db, project_id=pid, chapter_id=target.chapter_id,
        material_id=target.materials[0].utterance_id)
    await test_db.commit()
    issue_id, target_chapter_id, target_section_id = issue.id, target.chapter_id, target.id
    context = await structure.structure_context(test_db, plan, chapters, sections)
    session_id = None
    async def start_completion():
        nonlocal session_id
        response = await post_mobile_memoir_voice_session(client, project_id=pid,
            json={'guidance_mode': 'guided_interview', 'session_goal': 'book_completion', 'current_completion_issue_id': issue_id})
        assert response.status_code == 201
        session_id = response.json()['session']['id']
    _provider(monkeypatch, _merge(context), during_call=start_completion)
    with pytest.raises(HTTPException) as exc:
        await structure.review_story_structure(test_db, user_id=uid, project_id=pid)
    assert exc.value.status_code == 409
    await test_db.commit()
    current_issue = await test_db.get(MemoirCompletionIssue, issue_id, populate_existing=True)
    session = await test_db.get(MemoirVoiceSession, session_id, populate_existing=True)
    assert current_issue.chapter_id == target_chapter_id and current_issue.status == 'in_progress'
    assert session.current_completion_issue_id == issue_id
    plan, chapters, sections = await _load_tree(test_db, project_id=pid)
    assert next(s for s in sections if s.id == target_section_id).chapter_id == target_chapter_id
    fresh = await structure.structure_context(test_db, plan, chapters, sections)
    assert target_chapter_id in fresh.locked_chapters


@pytest.mark.db_independent
async def test_structure_merge_and_transcript_refresh_do_not_deadlock(client, test_db, monkeypatch):
    import asyncio
    from sqlalchemy import text
    from app.models.memoir import MemoirTranscriptVersion, MemoirVoiceUtterance
    from app.services.memoir.sections import refresh_material_transcript_version
    pid, uid = await _book(client, test_db)
    plan, chapters, sections = await _load_tree(test_db, project_id=pid)
    context = await structure.structure_context(test_db, plan, chapters, sections)
    utterance_id = sections[1].materials[0].utterance_id
    await test_db.commit()
    async with AsyncSession(test_db.bind, expire_on_commit=False) as automatic, AsyncSession(test_db.bind, expire_on_commit=False) as editor:
        editor_utterance = await editor.scalar(select(MemoirVoiceUtterance).where(
            MemoirVoiceUtterance.id == utterance_id).with_for_update())
        automatic_pid = await automatic.scalar(text('SELECT pg_backend_pid()'))
        _provider(monkeypatch, _merge(context, merge_sections=True))
        task = asyncio.create_task(structure.review_story_structure(automatic, user_id=uid, project_id=pid))
        try:
            async def wait_until_blocked():
                while not await test_db.scalar(text('SELECT cardinality(pg_blocking_pids(:pid)) > 0'), {'pid': automatic_pid}):
                    await asyncio.sleep(0.01)
            await asyncio.wait_for(wait_until_blocked(), timeout=3)
            version = MemoirTranscriptVersion(voice_session_id=editor_utterance.voice_session_id, source_artifact_id=utterance_id,
                version_type='user_edited', created_by='user', content='用户刚校正的讲述。')
            editor.add(version)
            await editor.flush()
            editor_utterance.transcript_edited = version.content
            await asyncio.wait_for(refresh_material_transcript_version(editor, project_id=pid, utterance_id=utterance_id,
                transcript_version_id=version.id), timeout=3)
            await editor.commit()
            with pytest.raises(HTTPException) as exc:
                await asyncio.wait_for(task, timeout=3)
            assert exc.value.status_code == 409
            await automatic.rollback()
            _, after_chapters, after_sections = await _load_tree(test_db, project_id=pid)
            assert len(after_chapters) == 4
            link = next(m for s in after_sections for m in s.materials if m.utterance_id == utterance_id)
            assert link.transcript_version_id == version.id
        finally:
            await editor.rollback()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await automatic.rollback()


@pytest.mark.db_independent
async def test_completion_start_racing_final_structure_write_cannot_leave_a_dangling_session(client, test_db, monkeypatch):
    import asyncio
    from sqlalchemy import text
    from app.models.memoir import MemoirVoiceSession
    from app.services.memoir import completion_interviews, completion_issues
    from tests.test_memoir_completion_issues_v2 import _completion_issue
    pid, uid = await _book(client, test_db)
    plan, chapters, sections = await _load_tree(test_db, project_id=pid)
    target = next(s for s in sections if s.chapter_id == chapters[1].id)
    issue = await _completion_issue(test_db, project_id=pid, chapter_id=target.chapter_id,
        material_id=target.materials[0].utterance_id)
    issue_id = issue.id
    await test_db.commit()
    context = await structure.structure_context(test_db, plan, chapters, sections)
    _provider(monkeypatch, _merge(context))
    apply = structure.apply_structure
    task = None
    async with AsyncSession(test_db.bind, expire_on_commit=False) as starter:
        starter_pid = await starter.scalar(text('SELECT pg_backend_pid()'))
        async def start():
            current_issue = await completion_issues.get_issue(starter, project_id=pid, issue_id=issue_id)
            session = MemoirVoiceSession(project_id=pid, session_goal='book_completion')
            starter.add(session)
            await starter.flush()
            await completion_interviews.start_issue_for_session(starter, issue=current_issue, voice_session=session)
            await starter.commit()
        async def apply_while_start_waits(*args, **kwargs):
            nonlocal task
            task = asyncio.create_task(start())
            async def wait_until_blocked():
                while not await test_db.scalar(text('SELECT cardinality(pg_blocking_pids(:pid)) > 0'), {'pid': starter_pid}):
                    await asyncio.sleep(0.01)
            await asyncio.wait_for(wait_until_blocked(), timeout=3)
            return await apply(*args, **kwargs)
        monkeypatch.setattr(structure, 'apply_structure', apply_while_start_waits)
        try:
            assert await structure.review_story_structure(test_db, user_id=uid, project_id=pid)
            with pytest.raises(HTTPException) as exc:
                await asyncio.wait_for(task, timeout=3)
            assert exc.value.status_code == 404
            await starter.rollback()
            assert not (await test_db.scalars(select(MemoirVoiceSession).where(
                MemoirVoiceSession.project_id == pid, MemoirVoiceSession.session_goal == 'book_completion'))).all()
        finally:
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await starter.rollback()


@pytest.mark.parametrize('automatic', [False, True])
async def test_only_automatic_review_routes_findings_to_structure_then_requeues_book(client, test_db, monkeypatch, automatic):
    from app.models.memoir import MemoirEditingJob
    from app.services.memoir import book_review_jobs, book_reviews
    pid, uid = await _book(client, test_db)
    plan, chapters, sections = await _load_tree(test_db, project_id=pid)
    context = await structure.structure_context(test_db, plan, chapters, sections)
    review = await _book_review_feedback(test_db, pid)
    async def generate(*args, **kwargs):
        return review
    monkeypatch.setattr(book_reviews, 'generate_or_reuse_book_review', generate)
    monkeypatch.setattr(book_reviews, 'get_current_book_review', generate)
    calls = _provider(monkeypatch, _merge(context))
    from app.clock import utcnow_naive
    job = MemoirEditingJob(project_id=pid, job_type='generate_book_review', status='running', run_after=utcnow_naive(),
        payload_json={'user_id': uid, 'apply_structure_guidance': automatic})
    test_db.add(job)
    await test_db.commit()
    result = await book_review_jobs.run_book_review_job(test_db, job)
    assert result.result_json['structure_updated'] == automatic
    _, chapters, _ = await _load_tree(test_db, project_id=pid)
    assert len(chapters) == (2 if automatic else 4)
    assert len(calls) == (1 if automatic else 0)
    if automatic:
        book_job = await test_db.scalar(select(MemoirEditingJob).where(
            MemoirEditingJob.project_id == pid, MemoirEditingJob.job_type == 'update_book_draft'))
        assert book_job.payload_json['source'] == 'structure_review'
        assert result.result_json['provider_request_pending'] is False


async def test_review_job_failure_cannot_commit_partial_directory_changes(client, test_db, monkeypatch):
    from app.clock import utcnow_naive
    from app.models.memoir import MemoirEditingJob
    from app.services.memoir import book_review_jobs, book_reviews
    pid, uid = await _book(client, test_db)
    plan, chapters, sections = await _load_tree(test_db, project_id=pid)
    context = await structure.structure_context(test_db, plan, chapters, sections)
    before = structure.material_identity(sections)
    review = await _book_review_feedback(test_db, pid)
    async def ready(*args, **kwargs):
        return review
    monkeypatch.setattr(book_reviews, 'generate_or_reuse_book_review', ready)
    monkeypatch.setattr(book_reviews, 'get_current_book_review', ready)
    _provider(monkeypatch, _merge(context))
    identity = structure.material_identity
    checks = 0
    def inconsistent(sections):
        nonlocal checks
        checks += 1
        return identity(sections) if checks == 1 else []
    monkeypatch.setattr(structure, 'material_identity', inconsistent)
    job = MemoirEditingJob(project_id=pid, job_type='generate_book_review', status='running',
        run_after=utcnow_naive(), payload_json={'user_id': uid, 'apply_structure_guidance': True})
    test_db.add(job)
    await test_db.commit()
    result = await book_review_jobs.run_book_review_job(test_db, job)
    assert result.status.value == 'failed'
    await test_db.commit()  # The handler returns failure; the worker persists its result.
    plan, chapters, sections = await _load_tree(test_db, project_id=pid)
    assert len(chapters) == 4 and identity(sections) == before
    assert structure.FEEDBACK_CHECKED_KEY not in plan.metadata_json


async def _mixed_section(client, db):
    from app.services.memoir.sections import merge_sections
    from app.schemas.memoir_sections import MemoirSectionMergeRequest
    pid, uid = await _book(client, db)
    plan, chapters, sections = await _load_tree(db, project_id=pid)
    target = next(s for s in sections if s.chapter_id == chapters[0].id)
    source = next(s for s in sections if s.chapter_id == chapters[1].id)
    await merge_sections(db, user_id=uid, project_id=pid, target_section_id=target.id,
        req=MemoirSectionMergeRequest(source_section_ids=[source.id]))
    for link in (await db.scalars(select(MemoirSectionMaterial).where(MemoirSectionMaterial.project_id == pid))).all():
        link.assigned_by = 'ai'
    plan.created_by = 'ai'
    await db.commit()
    return pid, uid


async def test_automatic_review_splits_an_existing_section_without_replacing_sources(client, test_db, monkeypatch):
    pid, uid = await _mixed_section(client, test_db)
    plan, chapters, sections = await _load_tree(test_db, project_id=pid)
    original = structure.material_identity(sections)
    mixed = next(s for s in sections if len(s.materials) == 2)
    moved_id = mixed.materials[-1].id
    context = await structure.structure_context(test_db, plan, chapters, sections)
    output = context.outline.model_dump()
    first = output['chapters'][0]
    first['sections'][0]['title'] = '妈妈的名字'
    first['sections'].append({'section_refs': ['T1'], 'title': '妈妈课堂里的童年'})
    output['section_splits'] = [{'source_section_ref': context.section_refs.encode(mixed.id),
        'new_section_ref': 'T1', 'material_refs': [context.material_refs.encode(moved_id)]}]
    _provider(monkeypatch, structure.StructureOutput.model_validate(output))
    assert await structure.review_story_structure(test_db, user_id=uid, project_id=pid)
    _, after_chapters, after = await _load_tree(test_db, project_id=pid)
    assert structure.material_identity(after) == original
    original_section = next(s for s in after if s.id == mixed.id)
    added = next(s for s in after if s.title == '妈妈课堂里的童年')
    assert [m.id for m in added.materials] == [moved_id]
    assert moved_id not in {m.id for m in original_section.materials}
    assert added.chapter_id == original_section.chapter_id == after_chapters[0].id


@pytest.mark.parametrize('protection', ['chapter_title', 'section_title', 'manual_assignment'])
async def test_field_protection_does_not_freeze_unrelated_sections(client, test_db, monkeypatch, protection):
    pid, uid = await _mixed_section(client, test_db)
    plan, chapters, sections = await _load_tree(test_db, project_id=pid)
    target = next(s for s in sections if s.chapter_id == chapters[0].id)
    protected_id = target.materials[0].id
    original_chapter_id, original_chapter_title, original_section_title = chapters[0].id, chapters[0].title, target.title
    if protection == 'chapter_title':
        chapters[0].metadata_json = {'title_updated_by': 'user'}
    elif protection == 'section_title':
        target.metadata_json = {'title_updated_by': 'user'}
    else:
        target.materials[0].assigned_by = 'user'
    await test_db.commit()
    context = await structure.structure_context(test_db, plan, chapters, sections)
    assert chapters[0].id not in context.locked_chapters
    output = context.outline.model_dump()
    group = output['chapters'][0]
    group['sections'].append({'section_refs': ['T1'], 'title': '课堂经历'})
    output['section_splits'] = [{'source_section_ref': context.section_refs.encode(target.id),
        'new_section_ref': 'T1', 'material_refs': [context.material_refs.encode(target.materials[-1].id)]}]
    _provider(monkeypatch, structure.StructureOutput.model_validate(output))
    assert await structure.review_story_structure(test_db, user_id=uid, project_id=pid)
    _, after_chapters, after = await _load_tree(test_db, project_id=pid)
    kept = next(s for s in after if s.id == target.id)
    assert after_chapters[0].title == original_chapter_title
    assert kept.title == original_section_title
    if protection == 'manual_assignment':
        link = next(m for m in kept.materials if m.id == protected_id)
        assert link.assigned_by == 'user' and kept.chapter_id == original_chapter_id


@pytest.mark.parametrize('protection', ['chapter_title', 'section_title', 'manual_assignment', 'excluded_material'])
async def test_protected_field_allows_other_sections_to_join_the_chapter(client, test_db, monkeypatch, protection):
    pid, uid = await _book(client, test_db)
    plan, chapters, sections = await _load_tree(test_db, project_id=pid)
    first = next(s for s in sections if s.chapter_id == chapters[0].id)
    original_chapter_title, original_section_title = chapters[0].title, first.title
    if protection == 'chapter_title':
        chapters[0].metadata_json = {'title_updated_by': 'user'}
    elif protection == 'section_title':
        first.metadata_json = {'title_updated_by': 'user'}
    elif protection == 'manual_assignment':
        first.materials[0].assigned_by = 'user'
    else:
        first.materials[0].include_in_book = False
    await test_db.commit()
    context = await structure.structure_context(test_db, plan, chapters, sections)
    output = context.outline.model_copy(deep=True)
    output.chapters[0].sections.extend(output.chapters.pop(1).sections)
    original = structure.material_identity(sections)
    _provider(monkeypatch, output)
    assert await structure.review_story_structure(test_db, user_id=uid, project_id=pid)
    _, after_chapters, after = await _load_tree(test_db, project_id=pid)
    assert len(after_chapters) == 3 and after_chapters[0].title == original_chapter_title
    assert next(s for s in after if s.id == first.id).title == original_section_title
    assert structure.material_identity(after) == original


@pytest.mark.parametrize('bad', ['all_materials', 'duplicate_material', 'foreign_material', 'missing_new_section',
    'duplicate_new_section', 'manual_assignment', 'manual_title', 'manual_draft'])
async def test_invalid_section_split_cannot_change_directory_or_memberships(client, test_db, monkeypatch, bad):
    from tests.memoir_helpers import create_test_chapter_draft
    pid, uid = await _mixed_section(client, test_db)
    plan, chapters, sections = await _load_tree(test_db, project_id=pid)
    mixed = next(s for s in sections if len(s.materials) == 2)
    if bad == 'manual_assignment':
        mixed.materials[-1].assigned_by = 'user'
    if bad == 'manual_title':
        mixed.metadata_json = {'title_updated_by': 'user'}
    if bad == 'manual_draft':
        await create_test_chapter_draft(test_db, project_id=pid, chapter_id=mixed.chapter_id,
            content='不能覆盖的人工正文。')
    await test_db.commit()
    context = await structure.structure_context(test_db, plan, chapters, sections)
    original = structure.material_identity(sections)
    output = context.outline.model_dump()
    refs = [context.material_refs.encode(mixed.materials[-1].id)]
    if bad == 'all_materials':
        refs = list(context.section_materials[context.section_refs.encode(mixed.id)])
    elif bad == 'duplicate_material':
        refs *= 2
    elif bad == 'foreign_material':
        refs = [context.material_refs.encode(next(s for s in sections if s.chapter_id == chapters[-1].id).materials[0].id)]
    output['section_splits'] = [{'source_section_ref': context.section_refs.encode(mixed.id),
        'new_section_ref': 'T1', 'material_refs': refs}]
    if bad != 'missing_new_section':
        output['chapters'][0]['sections'].append({'section_refs': ['T1'], 'title': '课堂经历'})
    if bad == 'duplicate_new_section':
        output['chapters'][0]['sections'].append({'section_refs': ['T1'], 'title': '重复的小节'})
    if bad == 'manual_title':
        output['chapters'][0]['sections'][0]['title'] = '覆盖人工标题'
    _provider(monkeypatch, structure.StructureOutput.model_validate(output))
    with pytest.raises(ValueError):
        await structure.review_story_structure(test_db, user_id=uid, project_id=pid)
    await test_db.commit()
    plan, after_chapters, after = await _load_tree(test_db, project_id=pid)
    assert (await structure.structure_context(test_db, plan, after_chapters, after)).outline == context.outline
    assert structure.material_identity(after) == original


async def test_section_split_can_create_a_chapter_and_rolls_back_on_later_failure(client, test_db, monkeypatch):
    pid, uid = await _mixed_section(client, test_db)
    plan, chapters, sections = await _load_tree(test_db, project_id=pid)
    mixed = next(s for s in sections if len(s.materials) == 2)
    context = await structure.structure_context(test_db, plan, chapters, sections)
    original = structure.material_identity(sections)
    moved_id = mixed.materials[-1].id
    output = context.outline.model_dump()
    output['chapters'].append({'chapter_ref': 'N1', 'title': '童年的课堂',
        'sections': [{'section_refs': ['T1'], 'title': '跟妈妈上课'}]})
    output['section_splits'] = [{'source_section_ref': context.section_refs.encode(mixed.id),
        'new_section_ref': 'T1', 'material_refs': [context.material_refs.encode(moved_id)]}]
    _provider(monkeypatch, structure.StructureOutput.model_validate(output))
    real_mark = structure.mark_section_structure_changed
    async def fail(*args, **kwargs):
        raise RuntimeError('failure after split and chapter creation')
    monkeypatch.setattr(structure, 'mark_section_structure_changed', fail)
    with pytest.raises(RuntimeError):
        await structure.review_story_structure(test_db, user_id=uid, project_id=pid)
    await test_db.commit()  # A worker may persist a failed job in this same session.
    plan, chapters, sections = await _load_tree(test_db, project_id=pid)
    assert (await structure.structure_context(test_db, plan, chapters, sections)).outline == context.outline
    assert structure.material_identity(sections) == original
    monkeypatch.setattr(structure, 'mark_section_structure_changed', real_mark)
    assert await structure.review_story_structure(test_db, user_id=uid, project_id=pid)
    _, after_chapters, after = await _load_tree(test_db, project_id=pid)
    assert structure.material_identity(after) == original
    new_chapter = next(c for c in after_chapters if c.title == '童年的课堂')
    assert [m.id for s in after if s.chapter_id == new_chapter.id for m in s.materials] == [moved_id]


@pytest.mark.parametrize('protection', ['chapter_title', 'section_title', 'manual_assignment'])
async def test_feedback_signature_tracks_field_protection_without_tracking_ai_titles(client, test_db, protection):
    pid, _ = await _book(client, test_db)
    plan, chapters, sections = await _load_tree(test_db, project_id=pid)
    before = await structure.structure_context(test_db, plan, chapters, sections)
    chapters[0].title = 'AI 调整后的主题'
    sections[0].title = 'AI 调整后的小节'
    await test_db.flush()
    ai_changed = await structure.structure_context(test_db, plan, chapters, sections)
    assert before.source_signature == ai_changed.source_signature
    if protection == 'chapter_title':
        chapters[0].metadata_json = {'title_updated_by': 'user'}
    elif protection == 'section_title':
        sections[0].metadata_json = {'title_updated_by': 'user'}
    else:
        sections[0].materials[0].assigned_by = 'user'
    await test_db.flush()
    protected = await structure.structure_context(test_db, plan, chapters, sections)
    assert before.source_signature != protected.source_signature


async def test_multiple_splits_from_one_section_preserve_all_memberships(client, test_db, monkeypatch):
    from app.services.memoir.sections import merge_sections
    from app.schemas.memoir_sections import MemoirSectionMergeRequest
    pid, uid = await _mixed_section(client, test_db)
    plan, chapters, sections = await _load_tree(test_db, project_id=pid)
    mixed = next(s for s in sections if len(s.materials) == 2)
    source = next(s for s in sections if s.chapter_id == chapters[2].id)
    await merge_sections(test_db, user_id=uid, project_id=pid, target_section_id=mixed.id,
        req=MemoirSectionMergeRequest(source_section_ids=[source.id]))
    for link in (await test_db.scalars(select(MemoirSectionMaterial).where(MemoirSectionMaterial.project_id == pid))).all():
        link.assigned_by = 'ai'
    plan.created_by = 'ai'
    await test_db.commit()
    plan, chapters, sections = await _load_tree(test_db, project_id=pid)
    mixed = next(s for s in sections if len(s.materials) == 3)
    original = structure.material_identity(sections)
    context = await structure.structure_context(test_db, plan, chapters, sections)
    output = context.outline.model_dump()
    output['section_splits'] = []
    for index, material in enumerate(mixed.materials[1:], start=1):
        ref = f'T{index}'
        output['section_splits'].append({'source_section_ref': context.section_refs.encode(mixed.id),
            'new_section_ref': ref, 'material_refs': [context.material_refs.encode(material.id)]})
        output['chapters'][0]['sections'].append({'section_refs': [ref], 'title': f'童年经历{index}'})
    from app.services.memoir import sections as section_service
    finalizations = []
    real_finalize = section_service.finalize_section_changes
    async def finalize(*args, **kwargs):
        finalizations.append(kwargs)
        await real_finalize(*args, **kwargs)
    monkeypatch.setattr(section_service, 'finalize_section_changes', finalize)
    _provider(monkeypatch, structure.StructureOutput.model_validate(output))
    assert await structure.review_story_structure(test_db, user_id=uid, project_id=pid)
    _, _, after = await _load_tree(test_db, project_id=pid)
    assert structure.material_identity(after) == original
    assert sorted(len(s.materials) for s in after) == [1, 1, 1, 1]
    assert len(finalizations) == 1
    assert finalizations[0]['source'] == 'structure_review' and finalizations[0]['settled'] is True
    assert finalizations[0]['section_ids'] >= {s.id for s in after if s.chapter_id == mixed.chapter_id}


async def test_split_prompt_uses_material_references_but_omits_excluded_text(client, test_db):
    pid, _ = await _mixed_section(client, test_db)
    plan, chapters, sections = await _load_tree(test_db, project_id=pid)
    mixed = next(s for s in sections if len(s.materials) == 2)
    excluded = mixed.materials[0]
    excluded.include_in_book = False
    await test_db.commit()
    context = await structure.structure_context(test_db, plan, chapters, sections)
    prompt = structure.build_structure_prompt(context, chapters=chapters, sections=sections,
        chapter_summaries={}, section_summaries={})
    import json
    payload, _ = json.JSONDecoder().raw_decode(prompt.split('当前目录：\n', 1)[1])
    material_payloads = [m for c in payload['chapters'] for s in c['sections'] for m in s['materials']]
    hidden = next(m for m in material_payloads if m['material_ref'] == context.material_refs.encode(excluded.id))
    assert hidden['assignment_locked'] and not hidden['included'] and 'text' not in hidden
    assert len(material_payloads) == sum(len(s.materials) for s in sections)
    assert all(m['text'] for m in material_payloads if m['included'])


async def test_manual_split_records_title_ownership_without_freezing_the_chapter(client, test_db, monkeypatch):
    pid, uid = await _mixed_section(client, test_db)
    plan, chapters, sections = await _load_tree(test_db, project_id=pid)
    mixed = next(s for s in sections if len(s.materials) == 2)
    chapter_id = mixed.chapter_id
    response = await client.post(f'/api/memoir/projects/{pid}/sections/{mixed.id}/split',
        json={'title': '我起的课堂故事名', 'utterance_ids': [mixed.materials[-1].utterance_id]})
    assert response.status_code == 200
    plan, chapters, sections = await _load_tree(test_db, project_id=pid)
    created = next(s for s in sections if s.title == '我起的课堂故事名')
    context = await structure.structure_context(test_db, plan, chapters, sections)
    assert chapter_id not in context.locked_chapters
    assert context.section_titles[context.section_refs.encode(created.id)] == '我起的课堂故事名'
    output = context.outline.model_copy(deep=True)
    chapter = next(c for c in output.chapters if context.chapter_refs.decode(c.chapter_ref) == chapter_id)
    chapter.title = '童年与妈妈'
    _provider(monkeypatch, output)
    assert await structure.review_story_structure(test_db, user_id=uid, project_id=pid)
    _, _, after = await _load_tree(test_db, project_id=pid)
    assert next(s for s in after if s.id == created.id).title == '我起的课堂故事名'


@pytest.mark.parametrize('destination', ['existing_chapter', 'new_chapter'])
async def test_split_cannot_leave_a_retained_chapter_with_only_excluded_material(client, test_db, monkeypatch, destination):
    pid, uid = await _mixed_section(client, test_db)
    plan, chapters, sections = await _load_tree(test_db, project_id=pid)
    mixed = next(s for s in sections if len(s.materials) == 2)
    mixed.materials[0].include_in_book = False
    await test_db.commit()
    original = structure.material_identity(sections)
    context = await structure.structure_context(test_db, plan, chapters, sections)
    output = context.outline.model_dump()
    output['section_splits'] = [{'source_section_ref': context.section_refs.encode(mixed.id),
        'new_section_ref': 'T1', 'material_refs': [context.material_refs.encode(mixed.materials[-1].id)]}]
    new_group = {'section_refs': ['T1'], 'title': '移出的故事'}
    if destination == 'new_chapter':
        output['chapters'].append({'chapter_ref': 'N1', 'title': '新主题', 'sections': [new_group]})
    else:
        output['chapters'][-1]['sections'].append(new_group)
    _provider(monkeypatch, structure.StructureOutput.model_validate(output))
    with pytest.raises(HTTPException) as exc:
        await structure.review_story_structure(test_db, user_id=uid, project_id=pid)
    assert exc.value.status_code == 409
    await test_db.commit()
    plan, after_chapters, after = await _load_tree(test_db, project_id=pid)
    assert (await structure.structure_context(test_db, plan, after_chapters, after)).outline == context.outline
    assert structure.material_identity(after) == original
