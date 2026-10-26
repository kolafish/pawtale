"""The model chooses memberships; the existing writer must enact them without scope drift."""
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models.memoir import MemoirSection, MemoirSectionMaterial
from app.services.memoir import chapter_structure as st
from app.services.memoir.structure_proposals import TargetDirectory, compile_region, finding_context
from app.schemas.memoir_editorial import BookStructureFinding
from app.services.memoir.story_tree_growth import _load_tree
from tests.memoir_helpers import create_test_book_plan_with_chapters, create_test_voice_transcript, ensure_mobile_memoir_project, memoir_source_ref


async def local_book(client, db):
    pid = await ensure_mobile_memoir_project(client)
    texts = ['离家上大学。', '周末给学生做家教赚钱。', '去外地给学生讲学习经验，挣一些报酬。',
             '讲演去过海边和山城。', '在山城讲演时和同伴拍了一张照片。', '后来到公司实习。', '我和同学合作做编程项目。', '小时候和奶奶一起做饭。']
    sources = [await create_test_voice_transcript(client, db, text, project_id=pid) for text in texts]
    plan, chapters = await create_test_book_plan_with_chapters(db, project_id=pid,
        chapter_titles=['大学经历', '童年'],
        source_refs_by_chapter=[[memoir_source_ref(s) for s in sources[:7]], [memoir_source_ref(sources[7])]])
    plan.created_by = 'ai'
    for c in chapters:
        c.metadata_json = {'created_from_material_assignment_by': 'ai'}
    _, chapters, sections = await _load_tree(db, project_id=pid)
    first = next(s for s in sections if s.chapter_id == chapters[0].id)
    first.title = '离家上大学'
    added = [MemoirSection(project_id=pid, chapter_id=chapters[0].id, title=title, sort_order=i + 1)
             for i, title in enumerate(['做家教', '大学期间赚钱的几种方式', '讲演地点和照片'])]
    db.add_all(added)
    await db.flush()
    destinations = [first, added[0], added[1], added[2], added[2], added[1], added[1]]
    by_source = {s['utterance_id']: (destination, i) for i, (s, destination) in enumerate(zip(sources, destinations))}
    for m in (await db.scalars(select(MemoirSectionMaterial).where(MemoirSectionMaterial.project_id == pid))).all():
        m.assigned_by = 'ai'
        if m.utterance_id in by_source:
            destination, i = by_source[m.utterance_id]
            m.section_id, m.sort_order = destination.id, i
    await db.commit()
    plan, chapters, sections = await _load_tree(db, project_id=pid)
    context = await st.structure_context(db, plan, chapters, sections)
    uid = (await client.get('/api/auth/me')).json()['id']
    return pid, uid, plan, chapters, sections, context


def mock_models(monkeypatch, answers):
    calls = []
    async def digest(*args, **kwargs):
        return None
    async def summaries(*args, **kwargs):
        return {}, {}, []
    async def generate(*args, **kwargs):
        calls.append(args[2])
        return SimpleNamespace(value=kwargs['output_model'].model_validate(answers.pop(0)))
    monkeypatch.setattr(st, 'generate_or_reuse_chapter_digest', digest)
    monkeypatch.setattr(st, 'load_structure_summaries', summaries)
    monkeypatch.setattr(st, 'get_ai_service', lambda _: None)
    monkeypatch.setattr(st, 'generate_structured_with_client', generate)
    return calls


async def test_review_after_manual_move_and_merge_preserves_surviving_order(client, test_db, monkeypatch):
    from tests.test_memoir_chapter_structure import _book

    pid, uid = await _book(client, test_db)
    _, chapters, _ = await _load_tree(test_db, project_id=pid)
    moved_id, removed_id, destination_id = chapters[-1].id, chapters[1].id, chapters[0].id
    endpoint = f'/api/mobile/memoir/projects/{pid}/book-plan/structure-commands'
    response = await client.post(endpoint, json={'type': 'move_chapter', 'chapter_id': moved_id, 'direction': 'up'})
    assert response.status_code == 200, response.text
    _, reordered, _ = await _load_tree(test_db, project_id=pid)
    expected_order = [c.id for c in reordered if c.id != removed_id]
    response = await client.post(endpoint, json={
        'type': 'merge_chapters', 'chapter_id': removed_id, 'target_chapter_id': destination_id,
    })
    assert response.status_code == 200, response.text
    plan, chapters, sections = await _load_tree(test_db, project_id=pid)
    assert removed_id in plan.metadata_json['manual_chapter_order']
    ctx = await st.structure_context(test_db, plan, chapters, sections)
    chapter = next(c for c in ctx.outline.chapters if ctx.chapter_refs.decode(c.chapter_ref) == moved_id)
    section = chapter.sections[0]
    original_materials = st.material_identity(sections)
    calls = mock_models(monkeypatch, [
        {'findings': [{'kind': 'title_scope', 'chapter_ids': [chapter.chapter_ref],
                       'section_ids': section.section_refs, 'reason': '标题应覆盖工作经历'}]},
        {'chapters': [{'chapter_ref': chapter.chapter_ref, 'title': chapter.title, 'sections': [
            {'title': '工作经历', 'material_refs': list(ctx.section_materials[section.section_refs[0]])},
        ]}]},
    ])
    assert await st.review_story_structure(test_db, user_id=uid, project_id=pid,
        chapter_ids=set(expected_order) - {destination_id})
    _, after_chapters, after_sections = await _load_tree(test_db, project_id=pid)
    assert len(calls) == 2
    assert [c.id for c in after_chapters] == expected_order
    assert st.material_identity(after_sections) == original_materials
    assert next(s for s in after_sections if s.id == ctx.section_refs.decode(section.section_refs[0])).title == '工作经历'


async def test_local_grouping_writes_model_decision_and_preserves_unaffected_structure(client, test_db, monkeypatch):
    pid, uid, plan, chapters, sections, ctx = await local_book(client, test_db)
    old = ctx.outline.chapters[0]
    mixed, places = old.sections[2:]
    mixed_refs = list(ctx.section_materials[mixed.section_refs[0]])
    places_refs = list(ctx.section_materials[places.section_refs[0]])
    finding = {'kind': 'section_structure', 'chapter_ids': [old.chapter_ref],
               'section_ids': [mixed.section_refs[0], places.section_refs[0]], 'reason': '讲演与地点属于同一经历，实习编程是另一组'}
    target = {'chapters': [{'chapter_ref': old.chapter_ref, 'title': '不应覆盖章名', 'sections': [
        {'title': '外地讲演', 'material_refs': [mixed_refs[0], *places_refs]},
        {'title': '实习与编程', 'material_refs': mixed_refs[1:]},
    ]}]}
    calls = mock_models(monkeypatch, [{'findings': [finding]}, target])
    original = st.material_identity(sections)
    stable = [(s.id, s.title, s.chapter_id, s.sort_order) for s in sections if ctx.section_refs.encode(s.id) not in finding['section_ids']]
    assert await st.review_story_structure(test_db, user_id=uid, project_id=pid, chapter_ids={chapters[0].id})
    _, after_chapters, after_sections = await _load_tree(test_db, project_id=pid)
    assert st.material_identity(after_sections) == original
    assert [(c.id, c.title) for c in after_chapters] == [(c.id, c.title) for c in chapters]
    by_id = {s.id: s for s in after_sections}
    assert [(sid, by_id[sid].title, by_id[sid].chapter_id, by_id[sid].sort_order) for sid, *_ in stable] == stable
    actual = {s.title: {ctx.material_refs.encode(m.id) for m in s.materials} for s in after_sections}
    assert actual['外地讲演'] == {mixed_refs[0], *places_refs}
    assert actual['实习与编程'] == set(mixed_refs[1:])
    diagnosis_input = json.JSONDecoder().raw_decode(calls[0].split('当前目录：\n')[1])[0]
    assert [c['chapter_ref'] for c in diagnosis_input['chapters']] == [old.chapter_ref]
    grouping_input = json.JSONDecoder().raw_decode(calls[1].split('素材与必要保护约束：')[1])[0]
    assert set(grouping_input['chapters'][0]['material_refs']) == set(mixed_refs + places_refs)
    assert grouping_input['chapters'][0]['protected_sections'] == []
    assert not await st.review_story_structure(test_db, user_id=uid, project_id=pid, chapter_ids={chapters[0].id})
    assert len(calls) == 2
    # A different scope is not incorrectly marked as already reviewed.
    calls = mock_models(monkeypatch, [{'findings': []}])
    assert not await st.review_story_structure(test_db, user_id=uid, project_id=pid, chapter_ids={chapters[1].id})
    assert len(calls) == 1


@pytest.mark.parametrize('invalid', [None, 'membership', 'outside_scope'])
async def test_title_only_review_cannot_change_memberships_or_scope(client, test_db, monkeypatch, invalid):
    pid, uid, _, chapters, sections, ctx = await local_book(client, test_db)
    chapter = ctx.outline.chapters[0]
    selected = chapter.sections[2]
    refs = list(ctx.section_materials[selected.section_refs[0]])
    finding = {'kind': 'title_scope', 'chapter_ids': [chapter.chapter_ref],
               'section_ids': selected.section_refs, 'reason': '名称应覆盖本节实际内容'}
    if invalid == 'outside_scope':
        finding.update(chapter_ids=[ctx.outline.chapters[1].chapter_ref], section_ids=[])
    target = {'chapters': [{'chapter_ref': chapter.chapter_ref, 'title': chapter.title, 'sections': [
        {'title': '讲演、实习与编程', 'material_refs': refs},
    ]}]}
    if invalid == 'membership':
        target['chapters'][0]['sections'] = [{'title': '讲演', 'material_refs': refs[:1]}, {'title': '实习', 'material_refs': refs[1:]}]
    mock_models(monkeypatch, [{'findings': [finding]}, target])
    if invalid:
        with pytest.raises(ValueError):
            await st.review_story_structure(test_db, user_id=uid, project_id=pid, chapter_ids={chapters[0].id})
    else:
        assert await st.review_story_structure(test_db, user_id=uid, project_id=pid, chapter_ids={chapters[0].id})
    _, _, after = await _load_tree(test_db, project_id=pid)
    assert st.material_identity(after) == st.material_identity(sections)
    assert {s.id: {m.id for m in s.materials} for s in after} == {s.id: {m.id for m in s.materials} for s in sections}
    assert next(s for s in after if s.id == ctx.section_refs.decode(selected.section_refs[0])).title == (
        selected.title if invalid else '讲演、实习与编程')


@pytest.mark.parametrize('invalid', ['missing', 'duplicate', 'manual_assignment'])
async def test_final_group_contract_rejects_material_loss_and_protection_violations(client, test_db, invalid):
    from dataclasses import replace
    pid, _, _, _, _, ctx = await local_book(client, test_db)
    chapter = ctx.outline.chapters[0]
    refs = chapter.sections[2].section_refs
    finding = BookStructureFinding(kind='section_structure', chapter_ids=[chapter.chapter_ref], section_ids=refs, reason='主题混杂')
    local = finding_context(ctx, finding)
    members = list(local.section_materials[refs[0]])
    target = TargetDirectory(chapters=[{'chapter_ref': chapter.chapter_ref, 'title': chapter.title, 'sections': [
        {'title': '讲演', 'material_refs': members[:1]}, {'title': '实习', 'material_refs': members[1:]},
    ]}])
    if invalid == 'missing':
        target.chapters[0].sections[-1].material_refs.pop()
    elif invalid == 'duplicate':
        target.chapters[0].sections[-1].material_refs.append(members[0])
    else:
        local = replace(local, fixed_materials={m: (refs[0], chapter.chapter_ref) for m in members})
    with pytest.raises(ValueError):
        compile_region(target, local, finding)
