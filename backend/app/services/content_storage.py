"""
Document content file storage.

Layout: {DATA_DIR}/projects/{project_id}/{document_id}.md
"""
import asyncio
import fcntl
import shutil
from contextlib import asynccontextmanager
import aiofiles
import aiofiles.os
from pathlib import Path
from app.config import DATA_DIR

PROJECTS_DIR = DATA_DIR / "projects"


@asynccontextmanager
async def project_content_lock(project_id: str):
    """Keep file writes and database rollback compensation under one owner.

    The lock survives transaction rollback and is shared by backend processes.
    Keep its inode outside the project directory so deletion cannot replace it.
    """
    lock_dir = PROJECTS_DIR / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    with (lock_dir / f"{project_id}.lock").open("a") as lock_file:
        while True:
            try:
                fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                await asyncio.sleep(0.01)
        try:
            yield
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def _doc_path(project_id: str, document_id: str) -> Path:
    return PROJECTS_DIR / project_id / f"{document_id}.md"


def content_exists(project_id: str, document_id: str) -> bool:
    return _doc_path(project_id, document_id).exists()


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
