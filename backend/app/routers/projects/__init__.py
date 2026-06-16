from fastapi import APIRouter

from . import activity, ai, core, git_sync, references, structure


router = APIRouter(tags=["projects"])
router.include_router(core.router, prefix="/api/projects")
router.include_router(structure.router, prefix="/api/projects")
router.include_router(references.router, prefix="/api/projects")
router.include_router(activity.router, prefix="/api/projects")
router.include_router(git_sync.router, prefix="/api/projects")
router.include_router(ai.router, prefix="/api/projects")
