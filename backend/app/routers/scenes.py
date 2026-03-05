from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models import Scene
from app.schemas import (
    SceneCreate, SceneUpdate, SceneResponse,
    SceneAIGenerateRequest, SceneAIGenerateResponse, SceneCandidate,
)
from app.services import ai_scene_service
from app.services.ai_scene import (
    AISceneParseError,
    AISceneProviderError,
    AISceneTimeoutError,
)

router = APIRouter(prefix="/api/scenes", tags=["scenes"])


# 预设场景数据
PRESET_SCENES = [
    {
        "name": "咖啡馆偶遇",
        "category": "初遇",
        "description": "你在常去的咖啡馆，发现TA坐在角落看书。阳光透过玻璃窗洒在TA身上，空气中弥漫着咖啡香。",
        "location": "街角咖啡馆",
        "time_weather": "午后，晴天",
        "mood_keywords": ["温馨", "心动", "偶遇"],
    },
    {
        "name": "雨天同伞",
        "category": "初遇",
        "description": "突然下起大雨，你们在便利店门口躲雨。TA只有一把伞，犹豫着要不要开口。",
        "location": "便利店门口",
        "time_weather": "傍晚，暴雨",
        "mood_keywords": ["浪漫", "暧昧", "雨天"],
    },
    {
        "name": "电梯困人",
        "category": "初遇",
        "description": "电梯突然停了，只有你们两个人。手机没信号，只能等待救援。",
        "location": "写字楼电梯",
        "time_weather": "工作日下午",
        "mood_keywords": ["紧张", "心跳", "密闭空间"],
    },
    {
        "name": "深夜已读不回",
        "category": "暧昧",
        "description": "凌晨两点，TA突然回复了你三小时前的消息。你盯着屏幕，心跳加速。",
        "location": "各自的房间",
        "time_weather": "深夜",
        "mood_keywords": ["暧昧", "心动", "期待"],
    },
    {
        "name": "借你外套",
        "category": "暧昧",
        "description": "天台风大，你冷得发抖。TA脱下外套披在你身上，带着TA的体温。",
        "location": "公司天台",
        "time_weather": "秋夜，微风",
        "mood_keywords": ["温暖", "心动", "保护"],
    },
    {
        "name": "机场拦截",
        "category": "表白",
        "description": "TA要出国了，你冲到机场想说出口。人群中，你看到了TA的背影。",
        "location": "国际机场",
        "time_weather": "清晨",
        "mood_keywords": ["紧张", "告白", "追逐"],
    },
    {
        "name": "烟花下告白",
        "category": "表白",
        "description": "跨年夜的烟花绽放，人群欢呼。TA转过头看着你，眼里映着光。",
        "location": "跨年活动现场",
        "time_weather": "跨年夜，烟花绽放",
        "mood_keywords": ["浪漫", "告白", "烟花"],
    },
    {
        "name": "同居第一天",
        "category": "甜蜜",
        "description": "搬进TA家的第一个早晨。阳光洒进来，TA在厨房做早餐，你假装还在睡。",
        "location": "TA的公寓",
        "time_weather": "周末早晨，晴天",
        "mood_keywords": ["甜蜜", "日常", "幸福"],
    },
    {
        "name": "误会分手",
        "category": "虐心",
        "description": "你看到TA和别人在一起，笑得很开心。你转身离开，没有回头。",
        "location": "商场",
        "time_weather": "周末下午",
        "mood_keywords": ["误会", "心痛", "分离"],
    },
    {
        "name": "多年后重逢",
        "category": "虐心",
        "description": "五年后的街头，你们再次相遇。TA看起来过得很好，你不知道该说什么。",
        "location": "城市街头",
        "time_weather": "秋天，落叶",
        "mood_keywords": ["重逢", "遗憾", "回忆"],
    },
]


@router.get("", response_model=list[SceneResponse])
async def get_scenes(db: AsyncSession = Depends(get_db)):
    """获取所有场景"""
    result = await db.execute(select(Scene).order_by(Scene.category, Scene.name))
    scenes = result.scalars().all()
    return scenes


@router.post("", response_model=SceneResponse)
async def create_scene(
    scene: SceneCreate,
    db: AsyncSession = Depends(get_db),
):
    """创建自定义场景"""
    db_scene = Scene(**scene.model_dump(), is_preset=False)
    db.add(db_scene)
    await db.commit()
    await db.refresh(db_scene)
    return db_scene


@router.get("/{scene_id}", response_model=SceneResponse)
async def get_scene(scene_id: str, db: AsyncSession = Depends(get_db)):
    """获取场景详情"""
    result = await db.execute(select(Scene).where(Scene.id == scene_id))
    scene = result.scalar_one_or_none()
    if not scene:
        raise HTTPException(status_code=404, detail="场景不存在")
    return scene


@router.put("/{scene_id}", response_model=SceneResponse)
async def update_scene(
    scene_id: str,
    scene_update: SceneUpdate,
    db: AsyncSession = Depends(get_db),
):
    """更新场景"""
    result = await db.execute(select(Scene).where(Scene.id == scene_id))
    scene = result.scalar_one_or_none()
    if not scene:
        raise HTTPException(status_code=404, detail="场景不存在")

    update_data = scene_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(scene, key, value)

    await db.commit()
    await db.refresh(scene)
    return scene


@router.delete("/{scene_id}")
async def delete_scene(scene_id: str, db: AsyncSession = Depends(get_db)):
    """删除场景"""
    result = await db.execute(select(Scene).where(Scene.id == scene_id))
    scene = result.scalar_one_or_none()
    if not scene:
        raise HTTPException(status_code=404, detail="场景不存在")

    await db.delete(scene)
    await db.commit()
    return {"message": "删除成功"}


@router.post("/init-presets")
async def init_preset_scenes(db: AsyncSession = Depends(get_db)):
    """初始化预设场景（仅首次运行）"""
    # 检查是否已有预设场景
    result = await db.execute(select(Scene).where(Scene.is_preset == True))
    existing = result.scalars().first()
    if existing:
        return {"message": "预设场景已存在"}

    # 创建预设场景
    for scene_data in PRESET_SCENES:
        scene = Scene(**scene_data, is_preset=True)
        db.add(scene)

    await db.commit()
    return {"message": f"已创建 {len(PRESET_SCENES)} 个预设场景"}


@router.post(
    "/ai-generate",
    response_model=SceneAIGenerateResponse,
    response_model_exclude_none=True,
)
async def ai_generate_scenes(request: SceneAIGenerateRequest):
    """AI 生成场景候选"""
    try:
        candidates, debug_info = await ai_scene_service.generate_scene_candidates(
            category=request.category,
            mood_keywords=request.mood_keywords,
            character_context=request.character_context,
            custom_prompt=request.custom_prompt,
            count=request.count,
            ai_model=request.ai_model,
            debug=request.debug,
        )

        return SceneAIGenerateResponse(
            candidates=[SceneCandidate(**c) for c in candidates],
            debug_info=debug_info if request.debug else None,
        )
    except AISceneParseError as e:
        detail: str | dict = f"场景生成失败：模型返回格式异常（{str(e)}）"
        if request.debug:
            detail = {"message": detail, "debug_info": getattr(e, "debug_info", None)}
        raise HTTPException(status_code=502, detail=detail)
    except AISceneTimeoutError as e:
        detail: str | dict = f"场景生成失败：模型响应超时（{str(e)}）"
        if request.debug:
            detail = {"message": detail, "debug_info": getattr(e, "debug_info", None)}
        raise HTTPException(status_code=504, detail=detail)
    except AISceneProviderError as e:
        detail: str | dict = f"场景生成失败：上游模型服务异常（{str(e)}）"
        if request.debug:
            detail = {"message": detail, "debug_info": getattr(e, "debug_info", None)}
        raise HTTPException(status_code=502, detail=detail)
    except Exception as e:
        detail: str | dict = f"场景生成失败: {str(e)}"
        if request.debug:
            detail = {"message": detail, "debug_info": None}
        raise HTTPException(status_code=500, detail=detail)
