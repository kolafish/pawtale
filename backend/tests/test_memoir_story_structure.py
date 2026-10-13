from types import SimpleNamespace

from fastapi import HTTPException
import pytest
from sqlalchemy import select

from app.models.memoir import MemoirBookPlanChapter, MemoirSection, MemoirSectionMaterial
from app.services.ai_references import ModelReferenceMap
from app.services.memoir import story_tree_growth as growth
from app.services.memoir.book_plan_sources import renumber_chapters, set_temporary_chapter_orders
from tests.test_memoir_book_completion import _book
from tests.test_memoir_section_organization import FakeStoryGrowthAI, _stopped_growth_job


def _normalize(payload, texts):
    return growth._normalize_groups(
        growth._GrowthOutput.model_validate(payload),
        utterances=[SimpleNamespace(id=str(i), transcript_raw=text, transcript_edited=None) for i, text in enumerate(texts)],
        chapter_refs=ModelReferenceMap('C', ['existing']), section_refs=ModelReferenceMap('S', ['section']),
    )


@pytest.mark.parametrize('payload', [
    {'new_chapters': [{'chapter_ref': 'N1', 'title': '童年'}], 'placements': {'1': {'kind': 'not_story'}}},
    {'placements': {'1': {'kind': 'story', 'chapter_ref': 'N9', 'new_section_title': '渡河'}}},
    {'placements': {'1': {'kind': 'story', 'section_id': 'S1', 'chapter_ref': 'C1'}}},
])
def test_invalid_new_chapter_reference_never_places_material(payload):
    with pytest.raises(HTTPException) as exc:
        _normalize(payload, ['有一次坐船去岛上吃了面。'])
    assert exc.value.status_code == 422


@pytest.mark.parametrize('anchor,evidence', [('C1', '八岁那年'), ('C99', '有一次')])
def test_unverifiable_optional_chronology_appends_without_rejecting_complete_material(anchor, evidence):
    groups = _normalize({
        'new_chapters': [{'chapter_ref': 'N1', 'title': '渡河',
                          'insert_before_chapter_id': anchor, 'time_evidence': evidence}],
        'placements': {'1': {'chapter_ref': 'N1', 'new_section_title': '岛上的面'}},
    }, ['有一次坐船去岛上吃了面。'])
    assert len(groups) == 1
    assert groups[0].utterance_ids == ['0']
    assert groups[0].action == 'create_chapter'
    assert groups[0].insert_before_chapter_id is None


async def test_one_new_chapter_keeps_distinct_events_and_places_before_existing_without_moving_old_order(client, test_db, monkeypatch):
    project_id, old_chapters, _ = await _book(client, test_db)
    created = []
    for text in ['我小时候在乡下住在河边。', '十二岁辍学，老师来家里劝。', '那个，我想想。']:
        source, job = await _stopped_growth_job(client, test_db, project_id, text)
        created.append(source)
    fake = FakeStoryGrowthAI({
        'new_chapters': [{'chapter_ref': 'N1', 'title': '乡下的童年', 'insert_before_chapter_id': 'C1', 'time_evidence': '我小时候在乡下'}],
        'placements': {
            '1': {'kind': 'story', 'chapter_ref': 'N1', 'new_section_title': '河边的家'},
            '2': {'kind': 'story', 'chapter_ref': 'N1', 'new_section_title': '辍学那年'},
            '3': {'kind': 'not_story'},
        },
    })
    monkeypatch.setattr(growth, 'get_ai_service', lambda _model=None: fake)
    result = await growth.run_story_tree_growth_job(test_db, job)
    await test_db.commit()
    chapters = list((await test_db.scalars(select(MemoirBookPlanChapter).where(
        MemoirBookPlanChapter.project_id == project_id,
    ).order_by(MemoirBookPlanChapter.sort_order))).all())
    assert chapters[0].title == '乡下的童年'
    assert [c.id for c in chapters[1:]] == [c.id for c in old_chapters]
    sections = list((await test_db.scalars(select(MemoirSection).where(MemoirSection.chapter_id == chapters[0].id)
                                          .order_by(MemoirSection.sort_order))).all())
    assert [s.title for s in sections] == ['河边的家', '辍学那年']
    links = list((await test_db.scalars(select(MemoirSectionMaterial).where(
        MemoirSectionMaterial.utterance_id.in_([s['utterance_id'] for s in created]),
    ))).all())
    assert {link.utterance_id: link.section_id for link in links} == {
        created[0]['utterance_id']: sections[0].id, created[1]['utterance_id']: sections[1].id,
    }
    assert result.result_json['growth']['deferred'][created[2]['utterance_id']]['status'] == 'not_story'
    job.result_json = result.result_json
    # A completed retry must neither add duplicate sections nor move the directory again.
    await growth.run_story_tree_growth_job(test_db, job)
    assert fake.calls == 1


@pytest.mark.parametrize('edit', ['reorder', 'rename', 'new_chapter'])
async def test_automatic_position_preserves_a_newer_directory_edit(client, test_db, edit):
    project_id, chapters, _ = await _book(client, test_db)
    new = MemoirBookPlanChapter(plan_id=chapters[0].plan_id, project_id=project_id, title='童年', sort_order=len(chapters))
    test_db.add(new)
    await test_db.flush()
    batch = growth.GrowthBatch(
        original_chapter_ids=[c.id for c in chapters], created_chapters={new.id: chapters[0].id},
        sources={}, section_versions={}, section_chapters={},
        chapter_updated_at={c.id: c.updated_at for c in [*chapters, new]},
    )
    if edit == 'reorder':
        set_temporary_chapter_orders([*chapters, new])
        await test_db.flush()
        renumber_chapters([*reversed(chapters), new])
    elif edit == 'rename':
        new.title = '我自己起的标题'
    else:
        test_db.add(MemoirBookPlanChapter(plan_id=new.plan_id, project_id=project_id, title='另一个新章', sort_order=len(chapters) + 1))
    await test_db.commit()
    async def order():
        return [(c.id, c.title) for c in (await test_db.scalars(select(MemoirBookPlanChapter).where(
            MemoirBookPlanChapter.project_id == project_id,
        ).order_by(MemoirBookPlanChapter.sort_order))).all()]
    before = await order()
    await growth._position_new_chapters(test_db, project_id=project_id, batch=batch)
    assert await order() == before


def test_each_whole_source_has_one_destination_and_shared_destinations_form_one_group():
    payload = {'new_chapters': [{'chapter_ref': 'N1', 'title': '童年往事'}], 'placements': {
        '1': {'chapter_ref': 'N1', 'new_section_title': '放学与复学'},
        '2': {'chapter_ref': 'N1', 'new_section_title': '放学与复学'},
        '3': {'kind': 'not_story'},
    }}
    groups = _normalize(payload, ['父亲接我放学。后来李老师劝我复学。', '父亲接我的那天下雨了。', '我想想。'])
    assert [(g.action, g.utterance_ids) for g in groups] == [('not_story', ['2']), ('create_chapter', ['0', '1'])]
    assert groups[1].new_section_title == '放学与复学'


@pytest.mark.parametrize('placements', [
    {'1': {'kind': 'not_story'}, '2': {'kind': 'not_story'}},
    {'01': {'kind': 'not_story'}},
    {'1': {'kind': 'not_story', 'section_id': 'S1'}},
    {'1': {'kind': 'unassigned', 'reason': ''}},
])
def test_source_map_rejects_invented_keys_and_ambiguous_nonstory_destinations(placements):
    with pytest.raises(HTTPException) as error:
        _normalize({'placements': placements}, ['父亲接我放学。后来李老师劝我复学。'])
    assert error.value.status_code == 422
