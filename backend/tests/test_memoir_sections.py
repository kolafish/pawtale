from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from app.database import Base, register_models
from app.domain.memoir.types import (
    MemoirGuidanceMode,
    MemoirSectionMaterialAssignedBy,
    MemoirSectionType,
)
from app.migrations.memoir_sections import (
    MEMOIR_SECTION_TABLES,
    assert_memoir_section_schema,
    migrate_memoir_section_schema,
)
from app.migrations.runtime import migrate_runtime_schema
from app.models.memoir import (
    MemoirBookPlan,
    MemoirBookPlanChapter,
    MemoirSection,
    MemoirSectionMaterial,
    MemoirVoiceSession,
    MemoirVoiceUtterance,
)
from app.models.projects import Project


async def _table_columns(conn, table_name: str) -> set[str]:
    rows = (await conn.execute(text(f"PRAGMA table_info({table_name})"))).fetchall()
    return {row[1] for row in rows}


async def _index_names(conn, table_name: str) -> set[str]:
    rows = (await conn.execute(text(f"PRAGMA index_list({table_name})"))).fetchall()
    return {row[1] for row in rows}


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


async def test_section_migration_creates_missing_tables():
    register_models()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            for table_name in reversed(MEMOIR_SECTION_TABLES):
                await conn.execute(text(f"DROP TABLE {table_name}"))

        result = await migrate_memoir_section_schema(engine)
        second_result = await migrate_memoir_section_schema(engine)

        assert result["changed_tables"] == list(MEMOIR_SECTION_TABLES)
        assert second_result["changed_tables"] == []
        await assert_memoir_section_schema(engine)

        async with engine.begin() as conn:
            assert "section_type" in await _table_columns(conn, "memoir_sections")
            assert "utterance_id" in await _table_columns(
                conn,
                "memoir_section_materials",
            )
            assert "input_signature" in await _table_columns(
                conn,
                "memoir_section_digests",
            )
    finally:
        await engine.dispose()


async def test_runtime_migration_creates_section_tables():
    register_models()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            for table_name in reversed(MEMOIR_SECTION_TABLES):
                await conn.execute(text(f"DROP TABLE {table_name}"))

        await migrate_runtime_schema(engine)
        await migrate_runtime_schema(engine)

        async with engine.begin() as conn:
            for table_name in MEMOIR_SECTION_TABLES:
                assert await _table_columns(conn, table_name)
    finally:
        await engine.dispose()


async def test_section_tables_define_constraints_and_indexes():
    register_models()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            section_sql = (
                await conn.execute(
                    text(
                        "SELECT sql FROM sqlite_master "
                        "WHERE type = 'table' AND name = 'memoir_sections'"
                    )
                )
            ).scalar_one()
            material_sql = (
                await conn.execute(
                    text(
                        "SELECT sql FROM sqlite_master "
                        "WHERE type = 'table' AND name = 'memoir_section_materials'"
                    )
                )
            ).scalar_one()
            digest_sql = (
                await conn.execute(
                    text(
                        "SELECT sql FROM sqlite_master "
                        "WHERE type = 'table' AND name = 'memoir_section_digests'"
                    )
                )
            ).scalar_one()

            assert "uq_memoir_sections_chapter_order" in section_sql
            assert "ck_memoir_sections_type" in section_sql
            assert "ck_memoir_sections_status" in section_sql
            assert "uq_memoir_section_materials_project_utterance" in material_sql
            assert "ck_memoir_section_materials_assigned_by" in material_sql
            assert "ck_memoir_section_materials_confidence" in material_sql
            assert "uq_memoir_section_digests_signature" in digest_sql
            assert "ck_memoir_section_digests_status" in digest_sql

            section_indexes = await _index_names(conn, "memoir_sections")
            material_indexes = await _index_names(
                conn,
                "memoir_section_materials",
            )
            digest_indexes = await _index_names(conn, "memoir_section_digests")
            assert "ix_memoir_sections_project_id" in section_indexes
            assert "ix_memoir_sections_chapter_id" in section_indexes
            assert "ix_memoir_section_materials_section_id" in material_indexes
            assert "ix_memoir_section_materials_utterance_id" in material_indexes
            assert "ix_memoir_section_digests_section_id" in digest_indexes
            assert "ix_memoir_section_digests_input_signature" in digest_indexes
    finally:
        await engine.dispose()


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
