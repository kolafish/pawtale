from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.domain.memoir.types import (
    MemoirGuidanceMode,
    MemoirSectionMaterialAssignedBy,
    MemoirSectionType,
)
from app.models.memoir import (
    MemoirBookPlan,
    MemoirBookPlanChapter,
    MemoirSection,
    MemoirSectionMaterial,
    MemoirVoiceSession,
    MemoirVoiceUtterance,
)
from app.models.projects import Project






async def _add_project_chapter_and_material(test_db):
    project = Project(title="回忆录项目", type="memoir")
    test_db.add(project)
    await test_db.flush()

    plan = MemoirBookPlan(project_id=project.id, title="章节规划")
    test_db.add(plan)
    await test_db.flush()

    chapter = MemoirBookPlanChapter(
        project_id=project.id,
        plan_id=plan.id,
        title="山桃花开的心动",
        sort_order=1,
    )
    test_db.add(chapter)

    voice_session = MemoirVoiceSession(
        project_id=project.id,
        guidance_mode=MemoirGuidanceMode.FREE_NARRATION.value,
    )
    test_db.add(voice_session)
    await test_db.flush()

    utterance = MemoirVoiceUtterance(
        voice_session_id=voice_session.id,
        transcript_raw="朋友发来山桃花照片，后来邀请我一起爬山。",
        transcript_edited="朋友发来山桃花照片，后来邀请我一起爬山。",
    )
    test_db.add(utterance)
    await test_db.flush()
    return project, chapter, utterance








async def test_one_material_has_one_section_membership(test_db):
    project, chapter, utterance = await _add_project_chapter_and_material(test_db)
    first = MemoirSection(
        project_id=project.id,
        chapter_id=chapter.id,
        title="重新联系",
        section_type=MemoirSectionType.EVENT.value,
        sort_order=1,
    )
    second = MemoirSection(
        project_id=project.id,
        chapter_id=chapter.id,
        title="第一次爬山",
        section_type=MemoirSectionType.EVENT.value,
        sort_order=2,
    )
    test_db.add_all([first, second])
    await test_db.flush()

    test_db.add(
        MemoirSectionMaterial(
            project_id=project.id,
            section_id=first.id,
            utterance_id=utterance.id,
            sort_order=1,
            assigned_by=MemoirSectionMaterialAssignedBy.USER.value,
        )
    )
    await test_db.commit()

    test_db.add(
        MemoirSectionMaterial(
            project_id=project.id,
            section_id=second.id,
            utterance_id=utterance.id,
            sort_order=1,
            assigned_by=MemoirSectionMaterialAssignedBy.AI.value,
        )
    )
    try:
        await test_db.flush()
    except IntegrityError:
        await test_db.rollback()
    else:
        raise AssertionError("one utterance must not belong to multiple sections")

    assert (
        await test_db.scalar(select(func.count()).select_from(MemoirSectionMaterial))
        == 1
    )
