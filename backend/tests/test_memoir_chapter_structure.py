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
    plan, _ = await create_test_book_plan_with_chapters(db, project_id=pid,
        chapter_titles=['妈妈的名字', '妈妈的课堂', '学前班', '工作'],
        source_refs_by_chapter=[[memoir_source_ref(s)] for s in sources])
    plan.created_by = 'ai'
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
        return SimpleNamespace(value=answer)
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


@pytest.mark.parametrize('bad', ['missing', 'duplicate', 'unknown', 'manual_title', 'excluded', 'manual_order'])
async def test_invalid_or_protected_structure_never_writes(client, test_db, monkeypatch, bad):
    pid, uid = await _book(client, test_db)
    plan, chapters, sections = await _load_tree(test_db, project_id=pid)
    if bad == 'manual_title':
        chapters[1].metadata_json = {'title_updated_by': 'user'}
    if bad == 'manual_order':
        chapters[0].metadata_json = chapters[3].metadata_json = {'title_updated_by': 'user'}
    if bad == 'excluded':
        next(s for s in sections if s.chapter_id == chapters[1].id).materials[0].include_in_book = False
    await test_db.commit()
    context = await structure.structure_context(test_db, plan, chapters, sections)
    output = _merge(context)
    if bad == 'manual_order':
        output = context.outline.model_copy(deep=True)
        output.chapters.reverse()
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
    from app.services.memoir.completion_issues import lock_project_chapters
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
