import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app.database import get_db
from app.models import Story, Character, Scene, StoryCharacter, StoryOutline
from app.schemas import (
    StoryCreate, StoryResponse, StoryUpdate,
    StoryGenerateRequest, StoryBranchRequest, StoryGenerateResponse,
    StoryNodeUpdateRequest, StoryNodeRegenerateRequest, StoryNodeReviseRequest, StoryExportResponse,
    StorySwitchSceneRequest, StoryUpdateCharactersRequest,
    PresentCharacter,
    RegenerateChoicesRequest, RegenerateChoicesResponse,
    PolishChaptersRequest, PolishChaptersResponse,
)
from app.services import get_ai_service

router = APIRouter(prefix="/api/stories", tags=["stories"])


@router.get("", response_model=list[StoryResponse])
async def get_stories(db: AsyncSession = Depends(get_db)):
    """获取所有故事"""
    result = await db.execute(
        select(Story)
        .options(selectinload(Story.characters))
        .order_by(Story.updated_at.desc())
    )
    stories = result.scalars().all()
    return stories


@router.post(
    "/generate",
    response_model=StoryGenerateResponse,
    response_model_exclude_none=True,
)
async def generate_story(
    request: StoryGenerateRequest,
    db: AsyncSession = Depends(get_db),
):
    """生成新故事（首段）"""
    # 获取角色信息（包含深度字段）
    characters = []
    for char_id in request.character_ids:
        result = await db.execute(select(Character).where(Character.id == char_id))
        char = result.scalar_one_or_none()
        if not char:
            raise HTTPException(status_code=404, detail=f"角色 {char_id} 不存在")
        characters.append({
            "name": char.name,
            "identity": char.identity,
            "personality": char.personality,
            "speaking_style": char.speaking_style,
            "background": char.background,
            # 深度字段
            "core_trait": char.core_trait,
            "fatal_flaw": char.fatal_flaw,
            "life_goal": char.life_goal,
        })

    # 获取场景信息（包含增强字段）
    result = await db.execute(select(Scene).where(Scene.id == request.scene_id))
    scene = result.scalar_one_or_none()
    if not scene:
        raise HTTPException(status_code=404, detail="场景不存在")

    scene_dict = {
        "name": scene.name,
        "description": scene.description,
        "location": scene.location,
        "time_weather": scene.time_weather,
        # 增强字段
        "sensory_details": scene.sensory_details,
        "props": scene.props,
        "dramatic_potential": scene.dramatic_potential,
    }

    # 获取大纲上下文（如果有）
    outline_context = None
    outline = None
    if request.outline_id:
        result = await db.execute(
            select(StoryOutline).where(StoryOutline.id == request.outline_id)
        )
        outline = result.scalar_one_or_none()
        if outline:
            outline_context = {
                "central_conflict": outline.central_conflict,
                "ending_type": outline.ending_type,
                "plot_hints": outline.plot_hints,
                "forbidden_elements": outline.forbidden_elements,
            }

    # 调用 AI 生成故事
    # 确定使用的AI模型
    ai_model = request.ai_model
    if not ai_model and request.outline_id and outline:
        ai_model = outline.ai_model

    selected_ai_model = ai_model or "deepseek"
    ai_service = get_ai_service(selected_ai_model)

    try:
        story_result = await ai_service.generate_story(
            characters=characters,
            scene=scene_dict,
            mood=request.mood,
            writing_style=request.writing_style,
            chapter_length=request.chapter_length,
            custom_direction=request.custom_direction,
            chapter_number=1,
            outline_context=outline_context,
            debug=request.debug,
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
            "ai_model": selected_ai_model,
            "choices": story_result["choices"],
        }
    }

    # 生成标题
    title = request.title
    if not title:
        char_names = "与".join([c["name"] for c in characters[:2]])
        title = f"{char_names}的{scene.name}"

    story = Story(
        title=title,
        scene_id=request.scene_id,
        mood=request.mood,
        writing_style=request.writing_style,
        chapter_length=request.chapter_length,
        ai_model=selected_ai_model,
        outline_id=request.outline_id,
        nodes=nodes,
        current_node_id=node_id,
        current_path=[node_id],
    )
    db.add(story)
    await db.flush()

    # 关联角色
    for char_id in request.character_ids:
        story_char = StoryCharacter(story_id=story.id, character_id=char_id)
        db.add(story_char)

    await db.commit()

    return StoryGenerateResponse(
        story_id=story.id,
        node_id=node_id,
        title=story_result.get("title"),
        content=story_result["content"],
        word_count=story_result.get("word_count"),
        ai_model=selected_ai_model,
        choices=story_result["choices"],
        debug_info=story_result.get("debug_info") if request.debug else None,
    )


@router.post(
    "/{story_id}/choose",
    response_model=StoryGenerateResponse,
    response_model_exclude_none=True,
)
async def choose_branch(
    story_id: str,
    request: StoryBranchRequest,
    db: AsyncSession = Depends(get_db),
):
    """选择分支继续生成（支持自定义方向）"""
    # 获取故事
    result = await db.execute(
        select(Story)
        .options(selectinload(Story.characters))
        .where(Story.id == story_id)
    )
    story = result.scalar_one_or_none()
    if not story:
        raise HTTPException(status_code=404, detail="故事不存在")

    # 获取当前节点
    current_node = story.nodes.get(story.current_node_id)
    if not current_node:
        raise HTTPException(status_code=400, detail="当前节点不存在")
    selected_ai_model = request.ai_model or story.ai_model or "deepseek"

    # 确定选择方式
    choice_label = None
    if request.custom_direction:
        # 用户自定义方向
        choice_label = f"自定义：{request.custom_direction}"
    elif request.choice_index is not None:
        # 验证选择
        if request.choice_index < 0 or request.choice_index >= len(current_node["choices"]):
            raise HTTPException(status_code=400, detail="无效的选择")
        choice = current_node["choices"][request.choice_index]
        choice_label = choice["label"]

        # 如果已有下一节点，直接返回
        if choice.get("next_node") and choice["next_node"] in story.nodes:
            next_node = story.nodes[choice["next_node"]]
            story.current_node_id = choice["next_node"]
            # 更新路径
            new_path = list(story.current_path or [])
            if choice["next_node"] not in new_path:
                new_path.append(choice["next_node"])
            story.current_path = new_path
            await db.commit()
            return StoryGenerateResponse(
                story_id=story.id,
                node_id=choice["next_node"],
                title=next_node.get("title"),
                content=next_node["content"],
                word_count=next_node.get("word_count"),
                ai_model=next_node.get("ai_model") or story.ai_model,
                choices=next_node["choices"],
            )
    else:
        raise HTTPException(status_code=400, detail="请选择一个分支或输入自定义方向")

    # 获取角色和场景信息（包含深度字段）
    characters = []
    for char in story.characters:
        characters.append({
            "name": char.name,
            "identity": char.identity,
            "personality": char.personality,
            "speaking_style": char.speaking_style,
            "background": char.background,
            # 深度字段
            "core_trait": char.core_trait,
            "fatal_flaw": char.fatal_flaw,
            "life_goal": char.life_goal,
        })

    result = await db.execute(select(Scene).where(Scene.id == story.scene_id))
    scene = result.scalar_one_or_none()
    scene_dict = {
        "name": scene.name if scene else "未知场景",
        "description": scene.description if scene else "",
        "location": scene.location if scene else None,
        "time_weather": scene.time_weather if scene else None,
        # 增强字段
        "sensory_details": scene.sensory_details if scene else None,
        "props": scene.props if scene else None,
    }

    # 获取大纲上下文（如果有）
    outline_context = None
    if story.outline_id:
        result = await db.execute(
            select(StoryOutline).where(StoryOutline.id == story.outline_id)
        )
        outline = result.scalar_one_or_none()
        if outline:
            outline_context = {
                "central_conflict": outline.central_conflict,
                "ending_type": outline.ending_type,
                "plot_hints": outline.plot_hints,
                "forbidden_elements": outline.forbidden_elements,
            }

    # 构建前情提要（更丰富的上下文）
    previous_content = f"{current_node['content']}\n\n用户选择了：{choice_label}"

    # 计算章节号
    chapter_number = len(story.nodes) + 1

    # 生成新内容
    ai_service = get_ai_service(selected_ai_model)

    try:
        story_result = await ai_service.generate_story(
            characters=characters,
            scene=scene_dict,
            mood=story.mood or "甜",
            writing_style=story.writing_style or "细腻",
            chapter_length=story.chapter_length or "中",
            previous_content=previous_content,
            custom_direction=request.custom_direction,
            chapter_number=chapter_number,
            outline_context=outline_context,
            debug=request.debug,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"故事生成失败: {str(e)}")

    # 创建新节点
    new_node_id = f"node_{len(story.nodes) + 1}"
    new_nodes = dict(story.nodes)
    new_nodes[new_node_id] = {
        "title": story_result.get("title") or f"第{chapter_number}章",
        "content": story_result["content"],
        "word_count": story_result.get("word_count", len(story_result["content"])),
        "ai_model": selected_ai_model,
        "choices": story_result["choices"],
    }

    # 更新选择的 next_node（如果是选择已有选项）
    if request.choice_index is not None:
        new_nodes[story.current_node_id]["choices"][request.choice_index]["next_node"] = new_node_id
    else:
        # 自定义方向，添加新选项
        custom_choice = {"label": choice_label, "next_node": new_node_id}
        new_nodes[story.current_node_id]["choices"].append(custom_choice)

    # 更新路径
    new_path = list(story.current_path or [])
    new_path.append(new_node_id)

    story.nodes = new_nodes
    story.current_node_id = new_node_id
    story.current_path = new_path
    await db.commit()

    return StoryGenerateResponse(
        story_id=story.id,
        node_id=new_node_id,
        title=story_result.get("title"),
        content=story_result["content"],
        word_count=story_result.get("word_count"),
        ai_model=selected_ai_model,
        choices=story_result["choices"],
        debug_info=story_result.get("debug_info") if request.debug else None,
    )


@router.get("/{story_id}", response_model=StoryResponse)
async def get_story(story_id: str, db: AsyncSession = Depends(get_db)):
    """获取故事详情"""
    result = await db.execute(
        select(Story)
        .options(selectinload(Story.characters))
        .where(Story.id == story_id)
    )
    story = result.scalar_one_or_none()
    if not story:
        raise HTTPException(status_code=404, detail="故事不存在")
    return story


@router.put("/{story_id}", response_model=StoryResponse)
async def update_story(
    story_id: str,
    request: StoryUpdate,
    db: AsyncSession = Depends(get_db),
):
    """更新故事基本信息"""
    result = await db.execute(
        select(Story)
        .options(selectinload(Story.characters))
        .where(Story.id == story_id)
    )
    story = result.scalar_one_or_none()
    if not story:
        raise HTTPException(status_code=404, detail="故事不存在")

    if request.title is not None:
        story.title = request.title
    if request.mood is not None:
        story.mood = request.mood
    if request.writing_style is not None:
        story.writing_style = request.writing_style
    if request.chapter_length is not None:
        story.chapter_length = request.chapter_length

    await db.commit()
    await db.refresh(story)
    return story


@router.put("/{story_id}/favorite")
async def toggle_favorite(story_id: str, db: AsyncSession = Depends(get_db)):
    """切换收藏状态"""
    result = await db.execute(select(Story).where(Story.id == story_id))
    story = result.scalar_one_or_none()
    if not story:
        raise HTTPException(status_code=404, detail="故事不存在")

    story.is_favorite = not story.is_favorite
    await db.commit()
    return {"is_favorite": story.is_favorite}


@router.delete("/{story_id}")
async def delete_story(story_id: str, db: AsyncSession = Depends(get_db)):
    """删除故事"""
    result = await db.execute(select(Story).where(Story.id == story_id))
    story = result.scalar_one_or_none()
    if not story:
        raise HTTPException(status_code=404, detail="故事不存在")

    await db.delete(story)
    await db.commit()
    return {"message": "删除成功"}


@router.post("/{story_id}/goto/{node_id}")
async def goto_node(
    story_id: str,
    node_id: str,
    db: AsyncSession = Depends(get_db),
):
    """跳转到指定节点（回溯功能）"""
    result = await db.execute(select(Story).where(Story.id == story_id))
    story = result.scalar_one_or_none()
    if not story:
        raise HTTPException(status_code=404, detail="故事不存在")

    if node_id not in story.nodes:
        raise HTTPException(status_code=404, detail="节点不存在")

    story.current_node_id = node_id

    # 更新路径到该节点
    current_path = story.current_path or []
    if node_id in current_path:
        # 回溯到之前的节点，截断路径
        idx = current_path.index(node_id)
        story.current_path = current_path[:idx + 1]
    else:
        # 跳转到新分支
        story.current_path = [node_id]

    await db.commit()

    node = story.nodes[node_id]
    return StoryGenerateResponse(
        story_id=story.id,
        node_id=node_id,
        title=node.get("title"),
        content=node["content"],
        word_count=node.get("word_count"),
        ai_model=node.get("ai_model") or story.ai_model,
        choices=node["choices"],
    )


@router.put("/{story_id}/nodes/{node_id}", response_model=StoryGenerateResponse)
async def update_node(
    story_id: str,
    node_id: str,
    request: StoryNodeUpdateRequest,
    db: AsyncSession = Depends(get_db),
):
    """编辑单个节点"""
    result = await db.execute(select(Story).where(Story.id == story_id))
    story = result.scalar_one_or_none()
    if not story:
        raise HTTPException(status_code=404, detail="故事不存在")

    if node_id not in story.nodes:
        raise HTTPException(status_code=404, detail="节点不存在")

    new_nodes = dict(story.nodes)
    node = dict(new_nodes[node_id])

    if request.title is not None:
        node["title"] = request.title
    if request.content is not None:
        node["content"] = request.content
        node["word_count"] = len(request.content)

    new_nodes[node_id] = node
    story.nodes = new_nodes
    await db.commit()

    return StoryGenerateResponse(
        story_id=story.id,
        node_id=node_id,
        title=node.get("title"),
        content=node["content"],
        word_count=node.get("word_count"),
        ai_model=node.get("ai_model") or story.ai_model,
        choices=node["choices"],
    )


@router.delete("/{story_id}/nodes/{node_id}")
async def delete_node(
    story_id: str,
    node_id: str,
    db: AsyncSession = Depends(get_db),
):
    """删除节点及其所有子节点"""
    result = await db.execute(select(Story).where(Story.id == story_id))
    story = result.scalar_one_or_none()
    if not story:
        raise HTTPException(status_code=404, detail="故事不存在")

    if node_id not in story.nodes:
        raise HTTPException(status_code=404, detail="节点不存在")

    if node_id == "node_1":
        raise HTTPException(status_code=400, detail="不能删除第一个节点")

    # 找出所有需要删除的节点（该节点及其所有子节点）
    nodes_to_delete = set()

    def find_children(nid: str):
        nodes_to_delete.add(nid)
        node = story.nodes.get(nid)
        if node:
            for choice in node.get("choices", []):
                next_node = choice.get("next_node")
                if next_node and next_node in story.nodes:
                    find_children(next_node)

    find_children(node_id)

    # 删除节点
    new_nodes = {k: v for k, v in story.nodes.items() if k not in nodes_to_delete}

    # 清理指向被删除节点的引用
    for nid, node in new_nodes.items():
        new_choices = []
        for choice in node.get("choices", []):
            if choice.get("next_node") not in nodes_to_delete:
                new_choices.append(choice)
            else:
                # 保留选项但清除 next_node
                new_choices.append({**choice, "next_node": None})
        new_nodes[nid] = {**node, "choices": new_choices}

    # 更新当前节点和路径
    if story.current_node_id in nodes_to_delete:
        # 回退到上一个有效节点
        current_path = story.current_path or []
        for nid in reversed(current_path):
            if nid not in nodes_to_delete:
                story.current_node_id = nid
                break
        else:
            story.current_node_id = "node_1"

    # 更新路径
    story.current_path = [nid for nid in (story.current_path or []) if nid not in nodes_to_delete]

    story.nodes = new_nodes
    await db.commit()

    return {"message": "删除成功", "deleted_nodes": list(nodes_to_delete)}


@router.post(
    "/{story_id}/nodes/{node_id}/regenerate",
    response_model=StoryGenerateResponse,
    response_model_exclude_none=True,
)
async def regenerate_node(
    story_id: str,
    node_id: str,
    request: StoryNodeRegenerateRequest | None = None,
    db: AsyncSession = Depends(get_db),
):
    """重新生成节点内容"""
    result = await db.execute(
        select(Story)
        .options(selectinload(Story.characters))
        .where(Story.id == story_id)
    )
    story = result.scalar_one_or_none()
    if not story:
        raise HTTPException(status_code=404, detail="故事不存在")

    if node_id not in story.nodes:
        raise HTTPException(status_code=404, detail="节点不存在")

    # 获取角色和场景信息
    characters = []
    for char in story.characters:
        characters.append({
            "name": char.name,
            "identity": char.identity,
            "personality": char.personality,
            "speaking_style": char.speaking_style,
            "background": char.background,
        })

    result = await db.execute(select(Scene).where(Scene.id == story.scene_id))
    scene = result.scalar_one_or_none()
    scene_dict = {
        "name": scene.name if scene else "未知场景",
        "description": scene.description if scene else "",
        "location": scene.location if scene else None,
        "time_weather": scene.time_weather if scene else None,
    }

    # 找到前一个节点的内容作为上下文
    previous_content = None
    current_path = story.current_path or []
    if node_id in current_path:
        idx = current_path.index(node_id)
        if idx > 0:
            prev_node_id = current_path[idx - 1]
            prev_node = story.nodes.get(prev_node_id)
            if prev_node:
                previous_content = prev_node["content"]

    # 计算章节号
    chapter_number = list(story.nodes.keys()).index(node_id) + 1

    # 重新生成
    selected_ai_model = (request.ai_model if request else None) or story.ai_model or "deepseek"
    ai_service = get_ai_service(selected_ai_model)

    try:
        story_result = await ai_service.generate_story(
            characters=characters,
            scene=scene_dict,
            mood=story.mood or "甜",
            writing_style=story.writing_style or "细腻",
            chapter_length=story.chapter_length or "中",
            previous_content=previous_content,
            chapter_number=chapter_number,
            debug=(request.debug if request else False),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"重新生成失败: {str(e)}")

    # 更新节点
    new_nodes = dict(story.nodes)
    old_node = new_nodes[node_id]
    new_nodes[node_id] = {
        "title": story_result.get("title") or old_node.get("title"),
        "content": story_result["content"],
        "word_count": story_result.get("word_count", len(story_result["content"])),
        "ai_model": selected_ai_model,
        "choices": story_result["choices"],  # 新的选项
    }

    story.nodes = new_nodes
    await db.commit()

    return StoryGenerateResponse(
        story_id=story.id,
        node_id=node_id,
        title=story_result.get("title"),
        content=story_result["content"],
        word_count=story_result.get("word_count"),
        ai_model=selected_ai_model,
        choices=story_result["choices"],
        debug_info=story_result.get("debug_info") if (request.debug if request else False) else None,
    )


@router.get("/{story_id}/export", response_model=StoryExportResponse)
async def export_story(
    story_id: str,
    db: AsyncSession = Depends(get_db),
):
    """导出故事全文（按当前路径）"""
    result = await db.execute(select(Story).where(Story.id == story_id))
    story = result.scalar_one_or_none()
    if not story:
        raise HTTPException(status_code=404, detail="故事不存在")

    # 按路径顺序导出
    path = story.current_path or list(story.nodes.keys())
    chapters = []
    total_words = 0

    for i, node_id in enumerate(path):
        node = story.nodes.get(node_id)
        if node:
            chapter = {
                "number": i + 1,
                "title": node.get("title") or f"第{i + 1}章",
                "content": node["content"],
                "word_count": node.get("word_count", len(node["content"])),
            }
            chapters.append(chapter)
            total_words += chapter["word_count"]

    return StoryExportResponse(
        title=story.title,
        chapters=chapters,
        total_words=total_words,
    )


@router.post(
    "/{story_id}/nodes/{node_id}/revise",
    response_model=StoryGenerateResponse,
    response_model_exclude_none=True,
)
async def revise_node(
    story_id: str,
    node_id: str,
    request: StoryNodeReviseRequest,
    db: AsyncSession = Depends(get_db),
):
    """根据修改意见重新生成节点内容"""
    result = await db.execute(
        select(Story)
        .options(selectinload(Story.characters))
        .where(Story.id == story_id)
    )
    story = result.scalar_one_or_none()
    if not story:
        raise HTTPException(status_code=404, detail="故事不存在")

    if node_id not in story.nodes:
        raise HTTPException(status_code=404, detail="节点不存在")

    # 获取原始内容
    original_node = story.nodes[node_id]
    original_content = original_node["content"]

    # 获取角色信息
    characters = []
    for char in story.characters:
        characters.append({
            "name": char.name,
            "identity": char.identity,
            "personality": char.personality,
            "speaking_style": char.speaking_style,
            "background": char.background,
        })

    # 调用 AI 修改
    selected_ai_model = request.ai_model or story.ai_model or "deepseek"
    ai_service = get_ai_service(selected_ai_model)

    try:
        story_result = await ai_service.revise_chapter(
            original_content=original_content,
            revision_instruction=request.instruction,
            characters=characters,
            writing_style=story.writing_style or "细腻",
            chapter_length=story.chapter_length or "中",
            debug=request.debug,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"修改失败: {str(e)}")

    # 更新节点
    new_nodes = dict(story.nodes)
    new_nodes[node_id] = {
        "title": story_result.get("title") or original_node.get("title"),
        "content": story_result["content"],
        "word_count": story_result.get("word_count", len(story_result["content"])),
        "ai_model": selected_ai_model,
        "choices": story_result["choices"],
    }

    story.nodes = new_nodes
    await db.commit()

    return StoryGenerateResponse(
        story_id=story.id,
        node_id=node_id,
        title=story_result.get("title"),
        content=story_result["content"],
        word_count=story_result.get("word_count"),
        ai_model=selected_ai_model,
        choices=story_result["choices"],
        debug_info=story_result.get("debug_info") if request.debug else None,
    )


@router.post(
    "/{story_id}/switch-scene",
    response_model=StoryGenerateResponse,
    response_model_exclude_none=True,
)
async def switch_scene(
    story_id: str,
    request: StorySwitchSceneRequest,
    db: AsyncSession = Depends(get_db),
):
    """切换故事场景并生成新章节"""
    # 获取故事
    result = await db.execute(
        select(Story)
        .options(selectinload(Story.characters))
        .where(Story.id == story_id)
    )
    story = result.scalar_one_or_none()
    if not story:
        raise HTTPException(status_code=404, detail="故事不存在")

    # 获取新场景
    result = await db.execute(select(Scene).where(Scene.id == request.scene_id))
    new_scene = result.scalar_one_or_none()
    if not new_scene:
        raise HTTPException(status_code=404, detail="场景不存在")

    # 获取当前节点
    current_node = story.nodes.get(story.current_node_id)
    if not current_node:
        raise HTTPException(status_code=400, detail="当前节点不存在")

    # 获取旧场景名称
    old_scene_name = current_node.get("scene_name", "未知场景")

    # 构建角色信息
    characters = []
    present_characters = []
    for char in story.characters:
        characters.append({
            "name": char.name,
            "identity": char.identity,
            "personality": char.personality,
            "speaking_style": char.speaking_style,
            "background": char.background,
        })
        present_characters.append({
            "character_id": char.id,
            "name": char.name,
            "role_type": char.role_type,
        })

    # 构建新场景信息
    scene_dict = {
        "name": new_scene.name,
        "description": new_scene.description,
        "location": new_scene.location,
        "time_weather": new_scene.time_weather,
        "dramatic_potential": new_scene.dramatic_potential,
        "sensory_details": new_scene.sensory_details,
        "props": new_scene.props,
    }

    # 构建前情提要，包含场景切换信息
    transition_desc = request.transition_type or "场景切换"
    previous_content = f"{current_node['content']}\n\n【{transition_desc}】从{old_scene_name}转到{new_scene.name}"

    # 计算章节号
    chapter_number = len(story.nodes) + 1

    # 生成新内容
    selected_ai_model = request.ai_model or story.ai_model or "deepseek"
    ai_service = get_ai_service(selected_ai_model)

    try:
        story_result = await ai_service.generate_story(
            characters=characters,
            scene=scene_dict,
            mood=story.mood or "甜",
            writing_style=story.writing_style or "细腻",
            chapter_length=story.chapter_length or "中",
            previous_content=previous_content,
            chapter_number=chapter_number,
            debug=request.debug,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"故事生成失败: {str(e)}")

    # 创建新节点
    new_node_id = f"node_{len(story.nodes) + 1}"
    new_nodes = dict(story.nodes)
    new_nodes[new_node_id] = {
        "title": story_result.get("title") or f"第{chapter_number}章",
        "content": story_result["content"],
        "word_count": story_result.get("word_count", len(story_result["content"])),
        "ai_model": selected_ai_model,
        "choices": story_result["choices"],
        "scene_id": new_scene.id,
        "scene_name": new_scene.name,
        "present_characters": present_characters,
        "scene_transition": {
            "from_scene": story.scene_id,
            "to_scene": new_scene.id,
            "transition_type": request.transition_type,
        },
    }

    # 在当前节点添加场景切换选项
    scene_switch_choice = {
        "label": f"场景切换：{new_scene.name}",
        "next_node": new_node_id,
    }
    new_nodes[story.current_node_id]["choices"].append(scene_switch_choice)

    # 更新路径
    new_path = list(story.current_path or [])
    new_path.append(new_node_id)

    story.nodes = new_nodes
    story.current_node_id = new_node_id
    story.current_path = new_path
    story.scene_id = new_scene.id  # 更新当前场景
    await db.commit()

    return StoryGenerateResponse(
        story_id=story.id,
        node_id=new_node_id,
        title=story_result.get("title"),
        content=story_result["content"],
        word_count=story_result.get("word_count"),
        ai_model=selected_ai_model,
        choices=story_result["choices"],
        scene_id=new_scene.id,
        scene_name=new_scene.name,
        present_characters=present_characters,
        debug_info=story_result.get("debug_info") if request.debug else None,
    )


@router.post("/{story_id}/update-characters", response_model=StoryResponse)
async def update_story_characters(
    story_id: str,
    request: StoryUpdateCharactersRequest,
    db: AsyncSession = Depends(get_db),
):
    """添加或移除故事角色"""
    # 获取故事
    result = await db.execute(
        select(Story)
        .options(selectinload(Story.characters))
        .where(Story.id == story_id)
    )
    story = result.scalar_one_or_none()
    if not story:
        raise HTTPException(status_code=404, detail="故事不存在")

    # 移除角色
    for char_id in request.remove_character_ids:
        result = await db.execute(
            select(StoryCharacter).where(
                StoryCharacter.story_id == story_id,
                StoryCharacter.character_id == char_id,
            )
        )
        story_char = result.scalar_one_or_none()
        if story_char:
            await db.delete(story_char)

    # 添加角色
    for char_id in request.add_character_ids:
        # 验证角色存在
        result = await db.execute(select(Character).where(Character.id == char_id))
        char = result.scalar_one_or_none()
        if not char:
            raise HTTPException(status_code=404, detail=f"角色 {char_id} 不存在")

        # 检查是否已关联
        result = await db.execute(
            select(StoryCharacter).where(
                StoryCharacter.story_id == story_id,
                StoryCharacter.character_id == char_id,
            )
        )
        if not result.scalar_one_or_none():
            story_char = StoryCharacter(story_id=story_id, character_id=char_id)
            db.add(story_char)

    await db.commit()

    # 重新查询以获取更新后的关联数据
    result = await db.execute(
        select(Story)
        .options(selectinload(Story.characters))
        .where(Story.id == story_id)
    )
    return result.scalar_one()


@router.post(
    "/{story_id}/nodes/{node_id}/regenerate-choices",
    response_model=RegenerateChoicesResponse,
    response_model_exclude_none=True,
)
async def regenerate_choices(
    story_id: str,
    node_id: str,
    request: RegenerateChoicesRequest,
    db: AsyncSession = Depends(get_db),
):
    """重新生成当前节点的分支选项"""
    # 获取故事
    result = await db.execute(
        select(Story)
        .options(selectinload(Story.characters))
        .where(Story.id == story_id)
    )
    story = result.scalar_one_or_none()
    if not story:
        raise HTTPException(status_code=404, detail="故事不存在")

    if node_id not in story.nodes:
        raise HTTPException(status_code=404, detail="节点不存在")

    # 获取当前节点内容
    current_node = story.nodes[node_id]
    current_content = current_node["content"]

    # 获取角色信息
    characters = []
    for char in story.characters:
        characters.append({
            "name": char.name,
            "personality": char.personality,
        })

    # 调用 AI 生成新选项
    selected_ai_model = request.ai_model or story.ai_model or "deepseek"
    ai_service = get_ai_service(selected_ai_model)

    try:
        regenerate_result = await ai_service.regenerate_choices(
            current_content=current_content,
            characters=characters,
            mood=story.mood or "甜",
            hint=request.hint,
            debug=request.debug,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"生成选项失败: {str(e)}")

    debug_info = None
    if isinstance(regenerate_result, dict):
        new_choices = regenerate_result.get("choices", [])
        debug_info = regenerate_result.get("debug_info")
    else:
        new_choices = regenerate_result

    # 更新节点的选项（保留已有的 next_node 链接）
    new_nodes = dict(story.nodes)
    old_choices = new_nodes[node_id].get("choices", [])

    # 保留已经有 next_node 的选项（已经续写过的）
    preserved_choices = [c for c in old_choices if c.get("next_node")]

    # 合并：保留的 + 新生成的
    new_nodes[node_id] = {
        **new_nodes[node_id],
        "last_choices_ai_model": selected_ai_model,
        "choices": preserved_choices + new_choices,
    }

    story.nodes = new_nodes
    await db.commit()

    return RegenerateChoicesResponse(
        choices=preserved_choices + new_choices,
        debug_info=debug_info if request.debug else None,
    )


@router.post(
    "/{story_id}/polish",
    response_model=PolishChaptersResponse,
    response_model_exclude_none=True,
)
async def polish_chapters(
    story_id: str,
    request: PolishChaptersRequest,
    db: AsyncSession = Depends(get_db),
):
    """整体润色选中的章节"""
    # 获取故事
    result = await db.execute(
        select(Story)
        .options(selectinload(Story.characters))
        .where(Story.id == story_id)
    )
    story = result.scalar_one_or_none()
    if not story:
        raise HTTPException(status_code=404, detail="故事不存在")

    # 验证节点存在
    chapters = []
    for node_id in request.node_ids:
        if node_id not in story.nodes:
            raise HTTPException(status_code=404, detail=f"节点 {node_id} 不存在")
        node = story.nodes[node_id]
        chapters.append({
            "node_id": node_id,
            "title": node.get("title", ""),
            "content": node.get("content", ""),
        })

    if len(chapters) < 1:
        raise HTTPException(status_code=400, detail="请至少选择一个章节")

    # 获取角色信息
    characters = []
    for char in story.characters:
        characters.append({
            "name": char.name,
            "personality": char.personality,
        })

    # 调用 AI 润色
    selected_ai_model = request.ai_model or story.ai_model or "deepseek"
    ai_service = get_ai_service(selected_ai_model)

    try:
        polish_result = await ai_service.polish_chapters(
            chapters=chapters,
            characters=characters,
            writing_style=story.writing_style or "细腻",
            instruction=request.instruction,
            debug=request.debug,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"润色失败: {str(e)}")

    debug_info = None
    if isinstance(polish_result, dict):
        polished_text = polish_result.get("polished_text", "")
        debug_info = polish_result.get("debug_info")
    else:
        polished_text = polish_result

    # 解析润色结果，按 "---" 分隔
    polished_parts = polished_text.split("---")

    # 构建响应
    polished_contents = []
    for i, node_id in enumerate(request.node_ids):
        if i < len(polished_parts):
            content = polished_parts[i].strip()
            # 移除可能的标题标记
            if content.startswith("【") and "】" in content:
                content = content.split("】", 1)[1].strip()
            polished_contents.append({
                "node_id": node_id,
                "content": content,
                "ai_model": selected_ai_model,
            })
        else:
            # 如果分隔不够，保留原内容
            polished_contents.append({
                "node_id": node_id,
                "content": chapters[i]["content"],
                "ai_model": selected_ai_model,
            })

    return PolishChaptersResponse(
        polished_contents=polished_contents,
        debug_info=debug_info if request.debug else None,
    )


@router.post("/{story_id}/apply-polish")
async def apply_polish(
    story_id: str,
    request: PolishChaptersResponse,
    db: AsyncSession = Depends(get_db),
):
    """应用润色结果到故事"""
    result = await db.execute(select(Story).where(Story.id == story_id))
    story = result.scalar_one_or_none()
    if not story:
        raise HTTPException(status_code=404, detail="故事不存在")

    new_nodes = dict(story.nodes)
    for item in request.polished_contents:
        node_id = item["node_id"]
        if node_id in new_nodes:
            new_nodes[node_id] = {
                **new_nodes[node_id],
                "content": item["content"],
                "word_count": len(item["content"]),
                "ai_model": item.get("ai_model", new_nodes[node_id].get("ai_model")),
            }

    story.nodes = new_nodes
    await db.commit()

    return {"message": "润色已应用", "updated_nodes": len(request.polished_contents)}
