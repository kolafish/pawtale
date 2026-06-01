from app.routers.auth import router as auth_router
from app.routers.characters import router as characters_router
from app.routers.scenes import router as scenes_router
from app.routers.projects import router as projects_router

__all__ = [
    "auth_router",
    "characters_router",
    "scenes_router",
    "projects_router",
]
