from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.database import init_db
from app.routers import characters_router, scenes_router, stories_router, outlines_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时初始化数据库
    await init_db()
    yield


app = FastAPI(
    title="Pawtale API",
    description="是我的CP呀 - AI故事创作平台",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(characters_router)
app.include_router(scenes_router)
app.include_router(stories_router)
app.include_router(outlines_router)


@app.get("/")
async def root():
    return {
        "name": "Pawtale API",
        "slogan": "是我的CP呀",
        "version": "0.1.0",
    }


@app.get("/health")
async def health():
    return {"status": "ok"}
