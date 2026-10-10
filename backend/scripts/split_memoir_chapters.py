from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select, text

from app.database import async_session_maker
from app.models.projects import Project
from app.models.users import User
from app.services.memoir.chapter_split import apply_chapter_split, decide_chapter_split
from app.services.memoir.project_ai_preferences import load_project_ai_model
from app.services.memoir.story_tree_growth import _load_tree


async def split_project(project_id: str, *, apply: bool) -> None:
    async with async_session_maker() as db:
        await db.execute(text("SET TRANSACTION READ ONLY"))
        project = await db.scalar(select(Project).where(Project.id == project_id))
        if project is None:
            print(f"{project_id}: 回忆录不存在")
            await db.rollback()
            return
        ai_model = await load_project_ai_model(db, project_id, None)
        _plan, chapters, sections = await _load_tree(db, project_id=project_id)
        db.expunge_all()
        await db.rollback()

    print(f"=== {project.title} ({project_id}) ===")
    proposals = []
    for chapter in chapters:
        chapter_sections = [section for section in sections if section.chapter_id == chapter.id]
        print(f"{chapter.title}（{len(chapter_sections)} 个小节）")
        groups = await decide_chapter_split(
            chapter=chapter,
            sections=chapter_sections,
            other_chapter_titles=[item.title for item in chapters if item.id != chapter.id],
            ai_model=ai_model,
        )
        if not groups:
            continue
        titles = {section.id: section.title for section in chapter_sections}
        print("  建议拆成：")
        for group in groups:
            print(f"    {group.title}：" + " / ".join(titles[section_id] for section_id in group.section_ids))
        proposals.append((chapter.id, groups))
    if not apply or not proposals:
        return
    async with async_session_maker() as db:
        for chapter_id, groups in proposals:
            await apply_chapter_split(
                db,
                user_id=project.user_id,
                project_id=project_id,
                chapter_id=chapter_id,
                groups=groups,
            )
        await db.commit()
    print("已拆分")


async def project_ids_for_user(name: str) -> list[str]:
    async with async_session_maker() as db:
        await db.execute(text("SET TRANSACTION READ ONLY"))
        ids = list((await db.scalars(
            select(Project.id)
            .join(User, User.id == Project.user_id)
            .where(User.display_name.ilike(name))
            .order_by(Project.created_at)
        )).all())
        await db.rollback()
        return ids


async def run(project_ids: list[str], user: str | None, *, apply: bool) -> None:
    if user:
        project_ids = [*project_ids, *await project_ids_for_user(user)]
    for project_id in project_ids:
        await split_project(project_id, apply=apply)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Propose (default, read-only) or apply AI chapter splits for memoir chapters with many sections."
    )
    parser.add_argument("--project-id", action="append", default=[])
    parser.add_argument("--user", help="split every memoir of users whose display name matches (ILIKE)")
    parser.add_argument("--apply", action="store_true", help="write the proposed splits")
    args = parser.parse_args()
    if not args.project_id and not args.user:
        parser.error("--project-id or --user is required")
    asyncio.run(run(args.project_id, args.user, apply=args.apply))


if __name__ == "__main__":
    main()
