"""A source utterance is indivisible when the initial tree is refined."""
import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.domain.memoir.types import MemoirCreatedBy
from app.models.memoir import MemoirSection, MemoirSectionMaterial
from app.models.projects import Project
from app.schemas.memoir_sections import MemoirSectionRegenerateRequest, MemoirSectionVersionReference
from app.services.memoir import section_regeneration as regeneration
from tests.test_memoir_book_completion import _book


@pytest.mark.ci_smoke
@pytest.mark.parametrize("case", ["initial", "multiple", "manual", "excluded"])
async def test_initial_indivisible_source_preserves_its_valid_section(client, test_db, monkeypatch, case):
    project_id, chapters, sources = await _book(client, test_db, chapter_count=2 if case == "multiple" else 1)
    project = await test_db.get(Project, project_id)
    chapter = chapters[0]
    section = await test_db.scalar(select(MemoirSection).where(MemoirSection.chapter_id == chapter.id))
    if case == "multiple":
        extra = await test_db.scalar(select(MemoirSectionMaterial).where(
            MemoirSectionMaterial.utterance_id == sources[1]["utterance_id"],
        ))
        extra.section_id = section.id
        extra.sort_order += 1
    if case == "excluded":
        material = await test_db.scalar(select(MemoirSectionMaterial).where(MemoirSectionMaterial.section_id == section.id))
        material.include_in_book = False
    await test_db.commit()
    original = (section.id, section.title, section.version)
    calls = []
    async def no_assignments(**kwargs):
        calls.append(len(kwargs["materials"]))
        # Exact production failure: candidate generation succeeded but the
        # sole indivisible source was not assigned to any candidate section.
        return [], [], "synthetic"
    monkeypatch.setattr(regeneration, "_plan_section_regeneration", no_assignments)
    async def run():
        return await regeneration.regenerate_chapter_sections(
            test_db, user_id=project.user_id, project_id=project_id, chapter_id=chapter.id,
            req=MemoirSectionRegenerateRequest(expected_sections=[
                MemoirSectionVersionReference(id=section.id, version=section.version),
            ]),
            structure_updated_by=MemoirCreatedBy.USER if case == "manual" else MemoirCreatedBy.AI,
        )
    if case == "initial":
        result = await run()
        assert result["assigned_count"] == 1
        assert result["section_count"] == 1
        assert not calls
    else:
        with pytest.raises(HTTPException) as caught:
            await run()
        assert caught.value.status_code == 409
        assert calls == [2 if case == "multiple" else 1]
    await test_db.refresh(section)
    assert (section.id, section.title, section.version) == original
