"""
Document content file storage.

Layout: {DATA_DIR}/projects/{project_id}/{document_id}.md
"""
import shutil
import aiofiles
import aiofiles.os
from pathlib import Path
from app.config import DATA_DIR

PROJECTS_DIR = DATA_DIR / "projects"


def _doc_path(project_id: str, document_id: str) -> Path:
    return PROJECTS_DIR / project_id / f"{document_id}.md"


async def ensure_project_dir(project_id: str) -> None:
    d = PROJECTS_DIR / project_id
    d.mkdir(parents=True, exist_ok=True)


async def read_content(project_id: str, document_id: str) -> str:
    p = _doc_path(project_id, document_id)
    if not p.exists():
        return ""
    async with aiofiles.open(p, mode="r", encoding="utf-8") as f:
        return await f.read()


async def write_content(project_id: str, document_id: str, content: str) -> None:
    await ensure_project_dir(project_id)
    p = _doc_path(project_id, document_id)
    async with aiofiles.open(p, mode="w", encoding="utf-8") as f:
        await f.write(content)


async def delete_content(project_id: str, document_id: str) -> None:
    p = _doc_path(project_id, document_id)
    if p.exists():
        await aiofiles.os.remove(p)


async def delete_project_dir(project_id: str) -> None:
    d = PROJECTS_DIR / project_id
    if d.exists():
        shutil.rmtree(d)


async def delete_group_documents(project_id: str, document_ids: list[str]) -> None:
    for doc_id in document_ids:
        await delete_content(project_id, doc_id)
