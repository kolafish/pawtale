"""
Migrate existing Pawtale data to the new project-based model.

Steps:
1. Back up the database
2. Create new tables (if not present)
3. Convert StoryWorkspaces (with content) → Projects with documents
4. Optionally create a demo project importing all global characters/scenes as references

Usage:
  cp data/pawtale.db data/pawtale.db.bak_premigration
  PYTHONPATH=backend backend/venv/bin/python backend/scripts/migrate_to_projects.py
"""

import asyncio
import shutil
import sys
import uuid
from datetime import datetime
from pathlib import Path

# Ensure backend is importable
backend_dir = Path(__file__).parent.parent
sys.path.insert(0, str(backend_dir))

from sqlalchemy import text, select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from app.config import get_settings
from app.database import Base
from app.models.models import Character, Scene, Story, StoryWorkspace
from app.models.projects import (
    Project, DocumentGroup, Document, ReferenceItem,
)

settings = get_settings()


async def main():
    db_path = settings.database_url.replace("sqlite+aiosqlite:///", "")
    backup_path = f"{db_path}.bak_premigration_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    print(f"[1/4] Backing up database → {backup_path}")
    shutil.copy2(db_path, backup_path)

    engine = create_async_engine(settings.database_url, echo=False)
    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    print("[2/4] Creating new tables...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_maker() as db:
        # Check if any projects already exist
        result = await db.execute(select(Project))
        existing = result.scalars().all()
        if existing:
            print(f"  Found {len(existing)} existing projects. Skipping workspace migration.")
        else:
            print("[3/4] Migrating StoryWorkspaces → Projects...")
            ws_result = await db.execute(
                select(StoryWorkspace).join(Story).order_by(Story.updated_at.desc())
            )
            workspaces = ws_result.scalars().all()

            migrated = 0
            for ws in workspaces:
                story_result = await db.execute(select(Story).where(Story.id == ws.story_id))
                story = story_result.scalar_one_or_none()
                if not story:
                    continue

                content = ws.editor_content or ""
                chapters = ws.chapters or []
                if not content.strip() and not any(
                    isinstance(ch, dict) and (ch.get("content") or "").strip()
                    for ch in chapters
                ):
                    continue

                project = Project(
                    id=str(uuid.uuid4()),
                    title=story.title or "未命名项目",
                    description=f"从旧故事 {story.id} 迁移",
                )
                db.add(project)
                await db.flush()

                group = DocumentGroup(
                    id=str(uuid.uuid4()),
                    project_id=project.id,
                    name="正文",
                    sort_order=0,
                )
                db.add(group)
                await db.flush()

                if chapters and len(chapters) > 0:
                    for i, ch in enumerate(chapters):
                        if not isinstance(ch, dict):
                            continue
                        doc = Document(
                            id=str(uuid.uuid4()),
                            group_id=group.id,
                            name=ch.get("title", f"第{i+1}章"),
                            content=ch.get("content", ""),
                            sort_order=i,
                        )
                        db.add(doc)
                else:
                    doc = Document(
                        id=str(uuid.uuid4()),
                        group_id=group.id,
                        name="正文",
                        content=content,
                        sort_order=0,
                    )
                    db.add(doc)

                migrated += 1

            await db.commit()
            print(f"  Migrated {migrated} workspaces into projects.")

        print("[4/4] Creating reference imports from global library...")
        # Check global characters and scenes
        char_result = await db.execute(select(Character))
        characters = char_result.scalars().all()
        scene_result = await db.execute(select(Scene))
        scenes = scene_result.scalars().all()

        if not characters and not scenes:
            print("  No global characters or scenes to import.")
        else:
            # Find first project (or skip)
            proj_result = await db.execute(select(Project).limit(1))
            first_project = proj_result.scalar_one_or_none()
            if first_project:
                # Check if already has references
                ref_result = await db.execute(
                    select(ReferenceItem).where(ReferenceItem.project_id == first_project.id).limit(1)
                )
                if ref_result.scalar_one_or_none():
                    print("  First project already has references, skipping import.")
                else:
                    for char in characters:
                        ref = ReferenceItem(
                            project_id=first_project.id,
                            type="character",
                            name=char.name,
                            content=char.background or "",
                            metadata_json={
                                "global_character_id": char.id,
                                "gender": char.gender,
                                "age": char.age,
                                "identity": char.identity,
                                "personality": char.personality or [],
                                "core_trait": char.core_trait,
                            },
                        )
                        db.add(ref)
                    for scene in scenes:
                        ref = ReferenceItem(
                            project_id=first_project.id,
                            type="location",
                            name=scene.name,
                            content=scene.description or "",
                            metadata_json={
                                "global_scene_id": scene.id,
                                "category": scene.category,
                                "location": scene.location,
                            },
                        )
                        db.add(ref)
                    await db.commit()
                    print(f"  Imported {len(characters)} characters and {len(scenes)} scenes as references.")
            else:
                print("  No projects to import into.")

    await engine.dispose()
    print("\nMigration complete!")


if __name__ == "__main__":
    asyncio.run(main())
