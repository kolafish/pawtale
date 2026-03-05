from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app.database import get_db
from app.models import (
    StoryOutline, OutlineCharacter, OutlineScene,
    Character, Scene, Story, StoryCharacter,
)
from app.schemas import (
    StoryOutlineCreate, StoryOutlineUpdate, StoryOutlineResponse,
    StoryOutlineGenerateRequest,
    OutlineCharacterCreate, OutlineCharacterUpdate, OutlineCharacterResponse,
    OutlineSceneCreate, OutlineSceneUpdate, OutlineSceneResponse,
    StoryGenerateResponse,
    OutlineEnhanceRequest, OutlineEnhanceResponse,
    OutlineTemplate, OutlineTemplatesResponse,
    OutlineGuidedCreateRequest, OutlineGuidedCreateResponse,
)
from app.services import get_ai_service

router = APIRouter(prefix="/api/outlines", tags=["outlines"])


# 预设模板数据
OUTLINE_TEMPLATES = [
    {
        "id": "sweet-campus",
        "name": "甜蜜校园",
        "description": "青涩美好的校园恋爱，从同桌到恋人",
        "mood": "甜",
        "writing_style": "轻松",
        "central_conflict": "学业压力与感情萌芽的冲突，家长反对早恋，两人需要在学习和感情之间找到平衡",
        "ending_type": "HE",
        "plot_hints": [
            "因座位调整成为同桌",
            "一起准备考试时产生好感",
            "被老师/家长发现关系",
            "高考前的约定",
            "毕业典礼的告白"
        ],
        "tags": ["校园", "青春", "暗恋", "同桌"]
    },
    {
        "id": "ceo-romance",
        "name": "霸总甜宠",
        "description": "高冷总裁与平凡女孩的甜蜜邂逅",
        "mood": "甜",
        "writing_style": "细腻",
        "central_conflict": "身份地位的悬殊让女主自卑退缩，男主需要打破她的心防，同时面对家族的反对",
        "ending_type": "HE",
        "plot_hints": [
            "意外相遇留下深刻印象",
            "因工作关系频繁接触",
            "男主开始主动制造偶遇",
            "身份曝光引发误会",
            "男主为爱放弃继承权/说服家族"
        ],
        "tags": ["都市", "霸总", "甜宠", "身份差"]
    },
    {
        "id": "bitter-love",
        "name": "虐恋情深",
        "description": "相爱却不能相守，虐心但有希望",
        "mood": "虐",
        "writing_style": "细腻",
        "central_conflict": "家族世仇或不可抗力让两人被迫分离，需要跨越重重障碍才能重逢",
        "ending_type": "HE",
        "plot_hints": [
            "相遇时不知彼此身份",
            "发现真相后的痛苦抉择",
            "被迫分离各自疗伤",
            "多年后意外重逢",
            "放下过去选择未来"
        ],
        "tags": ["虐恋", "误会", "重逢", "世仇"]
    },
    {
        "id": "ancient-romance",
        "name": "古风宫廷",
        "description": "宫墙深深，情深缘浅的古代爱情",
        "mood": "虐",
        "writing_style": "诗意",
        "central_conflict": "身处皇权漩涡，身不由己的命运让两人的感情充满变数",
        "ending_type": "OE",
        "plot_hints": [
            "初入宫廷的邂逅",
            "在权力斗争中相互扶持",
            "被迫卷入夺嫡之争",
            "生死关头的抉择",
            "天下与美人的取舍"
        ],
        "tags": ["古风", "宫廷", "权谋", "虐心"]
    },
    {
        "id": "healing-love",
        "name": "治愈系暖恋",
        "description": "两个受伤的灵魂相互治愈",
        "mood": "治愈",
        "writing_style": "细腻",
        "central_conflict": "各自背负过去的创伤，需要学会信任和敞开心扉",
        "ending_type": "HE",
        "plot_hints": [
            "在低谷期相遇",
            "发现彼此相似的伤痛",
            "慢慢建立信任",
            "帮助对方面对过去",
            "一起走向新生活"
        ],
        "tags": ["治愈", "温暖", "救赎", "慢热"]
    },
]


@router.get("/templates", response_model=OutlineTemplatesResponse)
async def get_outline_templates():
    """获取大纲模板列表"""
    templates = [OutlineTemplate(**t) for t in OUTLINE_TEMPLATES]
    return OutlineTemplatesResponse(templates=templates)


@router.get("", response_model=list[StoryOutlineResponse])
async def get_outlines(db: AsyncSession = Depends(get_db)):
    """获取所有故事大纲"""
    result = await db.execute(
        select(StoryOutline)
        .options(
            selectinload(StoryOutline.outline_characters).selectinload(OutlineCharacter.character),
            selectinload(StoryOutline.outline_scenes).selectinload(OutlineScene.scene),
        )
        .order_by(StoryOutline.updated_at.desc())
    )
    outlines = result.scalars().all()
    return outlines


@router.post("", response_model=StoryOutlineResponse)
async def create_outline(
    outline: StoryOutlineCreate,
    db: AsyncSession = Depends(get_db),
):
    """创建故事大纲"""
    db_outline = StoryOutline(**outline.model_dump())
    db.add(db_outline)
    await db.commit()
    await db.refresh(db_outline)

    # 重新查询以获取关联数据
    result = await db.execute(
        select(StoryOutline)
        .options(
            selectinload(StoryOutline.outline_characters).selectinload(OutlineCharacter.character),
            selectinload(StoryOutline.outline_scenes).selectinload(OutlineScene.scene),
        )
        .where(StoryOutline.id == db_outline.id)
    )
    return result.scalar_one()


@router.get("/{outline_id}", response_model=StoryOutlineResponse)
async def get_outline(outline_id: str, db: AsyncSession = Depends(get_db)):
    """获取大纲详情"""
    result = await db.execute(
        select(StoryOutline)
        .options(
            selectinload(StoryOutline.outline_characters).selectinload(OutlineCharacter.character),
            selectinload(StoryOutline.outline_scenes).selectinload(OutlineScene.scene),
        )
        .where(StoryOutline.id == outline_id)
    )
    outline = result.scalar_one_or_none()
    if not outline:
        raise HTTPException(status_code=404, detail="大纲不存在")
    return outline


@router.put("/{outline_id}", response_model=StoryOutlineResponse)
async def update_outline(
    outline_id: str,
    outline_update: StoryOutlineUpdate,
    db: AsyncSession = Depends(get_db),
):
    """更新大纲"""
    result = await db.execute(
        select(StoryOutline).where(StoryOutline.id == outline_id)
    )
    outline = result.scalar_one_or_none()
    if not outline:
        raise HTTPException(status_code=404, detail="大纲不存在")

    update_data = outline_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(outline, key, value)

    await db.commit()

    # 重新查询以获取关联数据
    result = await db.execute(
        select(StoryOutline)
        .options(
            selectinload(StoryOutline.outline_characters).selectinload(OutlineCharacter.character),
            selectinload(StoryOutline.outline_scenes).selectinload(OutlineScene.scene),
        )
        .where(StoryOutline.id == outline_id)
    )
    return result.scalar_one()


@router.delete("/{outline_id}")
async def delete_outline(outline_id: str, db: AsyncSession = Depends(get_db)):
    """删除大纲"""
    result = await db.execute(
        select(StoryOutline).where(StoryOutline.id == outline_id)
    )
    outline = result.scalar_one_or_none()
    if not outline:
        raise HTTPException(status_code=404, detail="大纲不存在")

    await db.delete(outline)
    await db.commit()
    return {"message": "删除成功"}


# 大纲角色管理
@router.post("/{outline_id}/characters", response_model=OutlineCharacterResponse)
async def add_character_to_outline(
    outline_id: str,
    char_config: OutlineCharacterCreate,
    db: AsyncSession = Depends(get_db),
):
    """添加角色到大纲"""
    # 验证大纲存在
    result = await db.execute(
        select(StoryOutline).where(StoryOutline.id == outline_id)
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="大纲不存在")

    # 验证角色存在
    result = await db.execute(
        select(Character).where(Character.id == char_config.character_id)
    )
    character = result.scalar_one_or_none()
    if not character:
        raise HTTPException(status_code=404, detail="角色不存在")

    # 检查是否已添加
    result = await db.execute(
        select(OutlineCharacter).where(
            OutlineCharacter.outline_id == outline_id,
            OutlineCharacter.character_id == char_config.character_id,
        )
    )
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="角色已在大纲中")

    # 创建关联
    outline_char = OutlineCharacter(
        outline_id=outline_id,
        **char_config.model_dump(),
    )
    db.add(outline_char)
    await db.commit()

    # 重新查询以获取关联数据
    result = await db.execute(
        select(OutlineCharacter)
        .options(selectinload(OutlineCharacter.character))
        .where(OutlineCharacter.id == outline_char.id)
    )
    return result.scalar_one()


@router.put("/{outline_id}/characters/{character_id}", response_model=OutlineCharacterResponse)
async def update_outline_character(
    outline_id: str,
    character_id: str,
    char_update: OutlineCharacterUpdate,
    db: AsyncSession = Depends(get_db),
):
    """更新大纲中的角色配置"""
    result = await db.execute(
        select(OutlineCharacter).where(
            OutlineCharacter.outline_id == outline_id,
            OutlineCharacter.character_id == character_id,
        )
    )
    outline_char = result.scalar_one_or_none()
    if not outline_char:
        raise HTTPException(status_code=404, detail="大纲角色配置不存在")

    update_data = char_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(outline_char, key, value)

    await db.commit()

    # 重新查询以获取关联数据
    result = await db.execute(
        select(OutlineCharacter)
        .options(selectinload(OutlineCharacter.character))
        .where(OutlineCharacter.id == outline_char.id)
    )
    return result.scalar_one()


@router.delete("/{outline_id}/characters/{character_id}")
async def remove_character_from_outline(
    outline_id: str,
    character_id: str,
    db: AsyncSession = Depends(get_db),
):
    """从大纲移除角色"""
    result = await db.execute(
        select(OutlineCharacter).where(
            OutlineCharacter.outline_id == outline_id,
            OutlineCharacter.character_id == character_id,
        )
    )
    outline_char = result.scalar_one_or_none()
    if not outline_char:
        raise HTTPException(status_code=404, detail="大纲角色配置不存在")

    await db.delete(outline_char)
    await db.commit()
    return {"message": "移除成功"}


# 大纲场景管理
@router.post("/{outline_id}/scenes", response_model=OutlineSceneResponse)
async def add_scene_to_outline(
    outline_id: str,
    scene_config: OutlineSceneCreate,
    db: AsyncSession = Depends(get_db),
):
    """添加场景到大纲"""
    # 验证大纲存在
    result = await db.execute(
        select(StoryOutline).where(StoryOutline.id == outline_id)
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="大纲不存在")

    # 验证场景存在
    result = await db.execute(
        select(Scene).where(Scene.id == scene_config.scene_id)
    )
    scene = result.scalar_one_or_none()
    if not scene:
        raise HTTPException(status_code=404, detail="场景不存在")

    # 检查是否已添加
    result = await db.execute(
        select(OutlineScene).where(
            OutlineScene.outline_id == outline_id,
            OutlineScene.scene_id == scene_config.scene_id,
        )
    )
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="场景已在大纲中")

    # 如果设置为起始场景，清除其他起始场景标记
    if scene_config.is_starting_scene:
        await db.execute(
            select(OutlineScene).where(
                OutlineScene.outline_id == outline_id,
                OutlineScene.is_starting_scene == True,
            )
        )
        # 更新所有现有起始场景为非起始
        result = await db.execute(
            select(OutlineScene).where(
                OutlineScene.outline_id == outline_id,
                OutlineScene.is_starting_scene == True,
            )
        )
        for existing in result.scalars().all():
            existing.is_starting_scene = False

    # 创建关联
    outline_scene = OutlineScene(
        outline_id=outline_id,
        **scene_config.model_dump(),
    )
    db.add(outline_scene)
    await db.commit()

    # 重新查询以获取关联数据
    result = await db.execute(
        select(OutlineScene)
        .options(selectinload(OutlineScene.scene))
        .where(OutlineScene.id == outline_scene.id)
    )
    return result.scalar_one()


@router.put("/{outline_id}/scenes/{scene_id}", response_model=OutlineSceneResponse)
async def update_outline_scene(
    outline_id: str,
    scene_id: str,
    scene_update: OutlineSceneUpdate,
    db: AsyncSession = Depends(get_db),
):
    """更新大纲中的场景配置"""
    result = await db.execute(
        select(OutlineScene).where(
            OutlineScene.outline_id == outline_id,
            OutlineScene.scene_id == scene_id,
        )
    )
    outline_scene = result.scalar_one_or_none()
    if not outline_scene:
        raise HTTPException(status_code=404, detail="大纲场景配置不存在")

    # 如果设置为起始场景，清除其他起始场景标记
    if scene_update.is_starting_scene:
        result = await db.execute(
            select(OutlineScene).where(
                OutlineScene.outline_id == outline_id,
                OutlineScene.is_starting_scene == True,
                OutlineScene.scene_id != scene_id,
            )
        )
        for existing in result.scalars().all():
            existing.is_starting_scene = False

    update_data = scene_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(outline_scene, key, value)

    await db.commit()

    # 重新查询以获取关联数据
    result = await db.execute(
        select(OutlineScene)
        .options(selectinload(OutlineScene.scene))
        .where(OutlineScene.id == outline_scene.id)
    )
    return result.scalar_one()


@router.delete("/{outline_id}/scenes/{scene_id}")
async def remove_scene_from_outline(
    outline_id: str,
    scene_id: str,
    db: AsyncSession = Depends(get_db),
):
    """从大纲移除场景"""
    result = await db.execute(
        select(OutlineScene).where(
            OutlineScene.outline_id == outline_id,
            OutlineScene.scene_id == scene_id,
        )
    )
    outline_scene = result.scalar_one_or_none()
    if not outline_scene:
        raise HTTPException(status_code=404, detail="大纲场景配置不存在")

    await db.delete(outline_scene)
    await db.commit()
    return {"message": "移除成功"}


# 基于大纲生成故事
@router.post("/{outline_id}/generate", response_model=StoryGenerateResponse)
async def generate_story_from_outline(
    outline_id: str,
    request: StoryOutlineGenerateRequest,
    db: AsyncSession = Depends(get_db),
):
    """基于大纲生成故事"""
    # 获取大纲及其关联数据
    result = await db.execute(
        select(StoryOutline)
        .options(
            selectinload(StoryOutline.outline_characters).selectinload(OutlineCharacter.character),
            selectinload(StoryOutline.outline_scenes).selectinload(OutlineScene.scene),
        )
        .where(StoryOutline.id == outline_id)
    )
    outline = result.scalar_one_or_none()
    if not outline:
        raise HTTPException(status_code=404, detail="大纲不存在")

    if not outline.outline_characters:
        raise HTTPException(status_code=400, detail="大纲中没有角色")

    if not outline.outline_scenes:
        raise HTTPException(status_code=400, detail="大纲中没有场景")

    # 确定起始场景
    starting_scene = None
    if request.starting_scene_id:
        for os in outline.outline_scenes:
            if os.scene_id == request.starting_scene_id:
                starting_scene = os.scene
                break
        if not starting_scene:
            raise HTTPException(status_code=400, detail="指定的起始场景不在大纲中")
    else:
        # 使用标记的起始场景或第一个场景
        for os in sorted(outline.outline_scenes, key=lambda x: (not x.is_starting_scene, x.sort_order)):
            starting_scene = os.scene
            break

    # 构建角色信息
    characters = []
    present_characters = []
    for oc in sorted(outline.outline_characters, key=lambda x: x.sort_order):
        char = oc.character
        char_dict = {
            "name": oc.story_name or char.name,
            "identity": char.identity,
            "personality": char.personality,
            "speaking_style": char.speaking_style,
            "background": oc.story_background or char.background,
            "role_type": oc.role_type,
            "core_trait": char.core_trait,
            "life_goal": oc.story_goal or char.life_goal,
            "fatal_flaw": char.fatal_flaw,
            "relationships": oc.relationships,
        }
        characters.append(char_dict)
        present_characters.append({
            "character_id": char.id,
            "name": oc.story_name or char.name,
            "role_type": oc.role_type,
        })

    # 构建场景信息
    scene_dict = {
        "name": starting_scene.name,
        "description": starting_scene.description,
        "location": starting_scene.location,
        "time_weather": starting_scene.time_weather,
        "dramatic_potential": starting_scene.dramatic_potential,
        "sensory_details": starting_scene.sensory_details,
        "props": starting_scene.props,
    }

    # 调用 AI 生成故事
    ai_service = get_ai_service(outline.ai_model)

    try:
        story_result = await ai_service.generate_story(
            characters=characters,
            scene=scene_dict,
            mood=outline.mood or "甜",
            writing_style=outline.writing_style or "细腻",
            chapter_length=outline.chapter_length or "中",
            custom_direction=request.custom_direction,
            chapter_number=1,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"故事生成失败: {str(e)}")

    # 创建故事记录
    node_id = "node_1"
    nodes = {
        node_id: {
            "title": story_result.get("title") or "第一章",
            "content": story_result["content"],
            "word_count": story_result.get("word_count", len(story_result["content"])),
            "choices": story_result["choices"],
            "scene_id": starting_scene.id,
            "scene_name": starting_scene.name,
            "present_characters": present_characters,
        }
    }

    # 生成标题
    char_names = "与".join([c["name"] for c in characters[:2]])
    title = f"{char_names}的故事"

    story = Story(
        title=title,
        outline_id=outline_id,
        scene_id=starting_scene.id,
        mood=outline.mood,
        writing_style=outline.writing_style,
        chapter_length=outline.chapter_length,
        ai_model=outline.ai_model,
        nodes=nodes,
        current_node_id=node_id,
        current_path=[node_id],
    )
    db.add(story)
    await db.flush()

    # 关联角色
    for oc in outline.outline_characters:
        story_char = StoryCharacter(story_id=story.id, character_id=oc.character_id)
        db.add(story_char)

    await db.commit()

    return StoryGenerateResponse(
        story_id=story.id,
        node_id=node_id,
        title=story_result.get("title"),
        content=story_result["content"],
        word_count=story_result.get("word_count"),
        choices=story_result["choices"],
        scene_id=starting_scene.id,
        scene_name=starting_scene.name,
        present_characters=present_characters,
    )


@router.post("/{outline_id}/ai-enhance", response_model=OutlineEnhanceResponse)
async def ai_enhance_outline(
    outline_id: str,
    request: OutlineEnhanceRequest,
    db: AsyncSession = Depends(get_db),
):
    """AI 生成大纲的核心冲突和剧情提示"""
    # 获取大纲及其关联数据
    result = await db.execute(
        select(StoryOutline)
        .options(
            selectinload(StoryOutline.outline_characters).selectinload(OutlineCharacter.character),
            selectinload(StoryOutline.outline_scenes).selectinload(OutlineScene.scene),
        )
        .where(StoryOutline.id == outline_id)
    )
    outline = result.scalar_one_or_none()
    if not outline:
        raise HTTPException(status_code=404, detail="大纲不存在")

    # 构建角色信息
    characters = []
    for oc in outline.outline_characters:
        char = oc.character
        characters.append({
            "name": oc.story_name or char.name,
            "role_type": oc.role_type,
            "personality": char.personality,
            "identity": char.identity,
            "core_trait": char.core_trait,
            "background": char.background,
        })

    # 构建场景信息
    scenes = []
    for os in outline.outline_scenes:
        scene = os.scene
        scenes.append({
            "name": scene.name,
            "category": scene.category,
        })

    # 调用 AI 服务
    ai_service = get_ai_service(outline.ai_model)

    try:
        result = await ai_service.enhance_outline(
            title=outline.title,
            mood=outline.mood,
            characters=characters if characters else None,
            scenes=scenes if scenes else None,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI 生成失败: {str(e)}")

    return OutlineEnhanceResponse(
        central_conflict=result.get("central_conflict", ""),
        plot_hints=result.get("plot_hints", []),
        suggested_mood=result.get("suggested_mood"),
        suggested_ending=result.get("suggested_ending"),
    )


@router.post("/guided-create", response_model=OutlineGuidedCreateResponse)
async def guided_create_outline(
    request: OutlineGuidedCreateRequest,
    db: AsyncSession = Depends(get_db),
):
    """引导式创建大纲 - 基于用户的故事目标、角色张力和核心冲突生成大纲草稿"""

    # 获取角色信息（如果提供）
    characters = []
    if request.character_ids:
        for char_id in request.character_ids:
            result = await db.execute(select(Character).where(Character.id == char_id))
            char = result.scalar_one_or_none()
            if char:
                characters.append({
                    "name": char.name,
                    "personality": char.personality,
                    "identity": char.identity,
                })

    # 调用 AI 生成大纲草稿
    ai_service = get_ai_service(request.ai_model)

    try:
        result = await ai_service.generate_outline_draft(
            story_goal=request.story_goal,
            character_tension=request.character_tension,
            central_conflict=request.central_conflict,
            characters=characters if characters else None,
            mood=request.mood,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"生成大纲草稿失败: {str(e)}")

    return OutlineGuidedCreateResponse(
        title=result.get("title", "未命名故事"),
        mood=result.get("mood", request.mood or "甜"),
        writing_style=result.get("writing_style", "细腻"),
        central_conflict=result.get("central_conflict", request.central_conflict),
        ending_type=result.get("ending_type", "HE"),
        plot_hints=result.get("plot_hints", []),
        forbidden_elements=result.get("forbidden_elements", []),
        story_goal=request.story_goal,
        character_tension=request.character_tension,
    )

