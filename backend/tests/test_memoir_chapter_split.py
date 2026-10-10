from types import SimpleNamespace

from sqlalchemy import select

from app.models.memoir import MemoirBookPlanChapter, MemoirSection, MemoirSectionMaterial
from app.models.projects import Project
from app.services.memoir import chapter_split
from tests.memoir_helpers import (
    create_test_book_plan_with_chapters,
    create_test_voice_transcript,
    ensure_mobile_memoir_project,
    memoir_source_ref,
)

TEXTS = ["小时候在村口放牛。", "后来考上县一中。", "高中住校吃咸菜。", "二十岁去当兵。", "部队里学会开车。", "退伍后进了运输公司。"]


async def _one_chapter_with_sections(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
    sources = [await create_test_voice_transcript(client, test_db, text, project_id=project_id) for text in TEXTS]
    _, chapters = await create_test_book_plan_with_chapters(
        test_db,
        project_id=project_id,
        chapter_titles=["老家的日子"],
        source_refs_by_chapter=[[memoir_source_ref(source) for source in sources]],
    )
    assert (await client.post(f"/api/memoir/projects/{project_id}/sections/bootstrap")).status_code == 200
    links = {
        link.utterance_id: link
        for link in (await test_db.scalars(select(MemoirSectionMaterial).where(
            MemoirSectionMaterial.project_id == project_id,
        ))).all()
    }
    for index, source in enumerate(sources, start=1):
        section = MemoirSection(project_id=project_id, chapter_id=chapters[0].id, title=TEXTS[index - 1], sort_order=index)
        test_db.add(section)
        await test_db.flush()
        links[source["utterance_id"]].section_id = section.id
    links[sources[-1]["utterance_id"]].include_in_book = False
    await test_db.commit()
    user_id = await test_db.scalar(select(Project.user_id).where(Project.id == project_id))
    return project_id, user_id, chapters[0], links[sources[-1]["utterance_id"]]


async def test_large_chapter_is_checked_once_per_size_and_split_by_whole_sections(client, test_db, monkeypatch):
    project_id, user_id, chapter, last_link = await _one_chapter_with_sections(client, test_db)
    replies = [
        [{"title": "", "section_ids": ["S1", "S2", "S3", "S4", "S5"]}],
        [
            {"title": "", "section_ids": ["S1", "S2", "S3"]},
            {"title": "当兵", "section_ids": ["S4", "S5"]},
            {"title": "运输公司", "section_ids": ["S6"]},
        ],
    ]
    calls = []
    async def generate(*args, **kwargs):
        calls.append(args[2])
        return SimpleNamespace(value=chapter_split._SplitOutput(groups=replies[len(calls) - 1]))
    monkeypatch.setattr(chapter_split, "get_ai_service", lambda model: None)
    monkeypatch.setattr(chapter_split, "generate_structured_with_client", generate)

    await chapter_split.split_large_chapters(test_db, user_id=user_id, project_id=project_id)
    await chapter_split.split_large_chapters(test_db, user_id=user_id, project_id=project_id)
    assert len(calls) == 1

    last_link.include_in_book = True
    await test_db.commit()
    await chapter_split.split_large_chapters(test_db, user_id=user_id, project_id=project_id)
    assert len(calls) == 2

    chapters = (await test_db.scalars(select(MemoirBookPlanChapter).where(
        MemoirBookPlanChapter.project_id == project_id,
    ).order_by(MemoirBookPlanChapter.sort_order))).all()
    assert [item.title for item in chapters] == ["老家的日子", "当兵"]
    moved = (await test_db.scalars(select(MemoirSection.title).where(
        MemoirSection.chapter_id == chapters[1].id,
    ).order_by(MemoirSection.sort_order))).all()
    assert moved == TEXTS[3:]
