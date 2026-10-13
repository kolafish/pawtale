"""Automatic refinement must not undo an already valid initial placement."""

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.domain.memoir.types import MemoirCreatedBy
from app.models.memoir import MemoirSection, MemoirSectionMaterial, MemoirVoiceUtterance
from app.models.projects import Project
from app.schemas.memoir_sections import MemoirSectionRegenerateRequest, MemoirSectionVersionReference
from app.services.memoir import section_regeneration as regeneration
from tests.test_memoir_book_completion import _book
from tests.test_memoir_section_organization import ReplayMaterialOrganizationAI


@pytest.mark.ci_smoke
@pytest.mark.parametrize("case", [
    "two_all_pending", "three_all_pending", "partial", "excluded_material",
    "complete", "manual", "missing_coverage", "duplicate_coverage",
    "concurrent_edit", "concurrent_transcript", "no_original_placement",
])
async def test_initial_refinement_preserves_complete_existing_placement(client, test_db, monkeypatch, case):
    count = 2 if case == "two_all_pending" else 3
    project_id, chapters, _ = await _book(client, test_db, chapter_count=count)
    project = await test_db.get(Project, project_id)
    chapter_id = chapters[0].id
    sections = list((await test_db.scalars(select(MemoirSection).where(
        MemoirSection.project_id == project_id,
    ).order_by(MemoirSection.sort_order, MemoirSection.id))).all())
    for index, section in enumerate(sections):
        section.chapter_id = chapter_id
        section.sort_order = index + 10
    materials = list((await test_db.scalars(select(MemoirSectionMaterial).where(
        MemoirSectionMaterial.project_id == project_id,
    ).order_by(MemoirSectionMaterial.id))).all())
    if case == "two_all_pending":
        for index, material in enumerate(materials):
            material.section_id = sections[0].id
            material.sort_order = index + 10
    if case == "excluded_material":
        materials[0].include_in_book = False
    if case == "no_original_placement":
        sections[0].include_in_book = False
    await test_db.commit()
    before_sections = [(s.id, s.title, s.version, s.sort_order, s.include_in_book) for s in sections]
    before_materials = [(m.id, m.utterance_id, m.section_id, m.include_in_book) for m in materials]
    req = MemoirSectionRegenerateRequest(expected_sections=[
        MemoirSectionVersionReference(id=s.id, version=s.version) for s in sections
    ])

    class Organizer(ReplayMaterialOrganizationAI):
        def _should_leave_unassigned(self, index):
            if case == "complete":
                return False
            return index > 0 if case == "partial" else True

    fake = Organizer()
    monkeypatch.setattr(regeneration, "get_ai_service", lambda _model: fake)
    if case == "missing_coverage":
        async def missing_coverage(**kwargs):
            return [], [], "synthetic"
        monkeypatch.setattr(regeneration, "_plan_section_regeneration", missing_coverage)
    if case == "duplicate_coverage":
        planner = regeneration._plan_section_regeneration
        async def duplicate_coverage(**kwargs):
            planned, pending, model = await planner(**kwargs)
            return planned, [*pending, pending[0]], model
        monkeypatch.setattr(regeneration, "_plan_section_regeneration", duplicate_coverage)

    async def progress(state):
        if case == "concurrent_edit" and state.get("stage") == "saving":
            sections[0].version += 1
            await test_db.commit()
        if case == "concurrent_transcript" and state.get("stage") == "saving":
            utterance = await test_db.get(MemoirVoiceUtterance, materials[0].utterance_id)
            utterance.transcript_edited = "用户刚刚纠正了这条讲述。"
            await test_db.commit()

    async def run():
        return await regeneration.regenerate_chapter_sections(
            test_db, user_id=project.user_id, project_id=project_id, chapter_id=chapter_id,
            req=req, progress_callback=progress,
            structure_updated_by=MemoirCreatedBy.USER if case == "manual" else MemoirCreatedBy.AI,
        )

    if case in {
        "manual", "missing_coverage", "duplicate_coverage", "concurrent_edit",
        "concurrent_transcript", "no_original_placement",
    }:
        with pytest.raises(HTTPException) as caught:
            await run()
        assert caught.value.status_code == 409
        if case in {"concurrent_edit", "concurrent_transcript"}:
            assert isinstance(caught.value, regeneration.SectionRegenerationConflict)
    else:
        result = await run()
        assert result["assigned_count"] == count
        assert result["unassigned_count"] == 0
        assert len(fake.prompts) == 2

    current = await regeneration._load_chapter(test_db, project_id=project_id, chapter_id=chapter_id)
    if case == "complete":
        assert len(current.sections) == 1
        assert current.sections[0].id not in {s[0] for s in before_sections}
        assert {m.utterance_id for m in current.sections[0].materials} == {m[1] for m in before_materials}
        return
    if case == "concurrent_edit":
        before_sections[0] = (*before_sections[0][:2], before_sections[0][2] + 1, *before_sections[0][3:])
    assert sorted((s.id, s.title, s.version, s.sort_order, s.include_in_book) for s in current.sections) == sorted(before_sections)
    assert sorted((m.id, m.utterance_id, m.section_id, m.include_in_book) for s in current.sections for m in s.materials) == sorted(before_materials)
