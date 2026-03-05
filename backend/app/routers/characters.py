from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models import Character
from app.schemas import (
    CharacterCreate, CharacterUpdate, CharacterResponse,
    AvatarGenerateRequest, AvatarGenerateResponse,
    CharacterAutoCompleteRequest, CharacterAutoCompleteResponse,
)
from app.services import ai_image_service, get_ai_service

router = APIRouter(prefix="/api/characters", tags=["characters"])


@router.get("", response_model=list[CharacterResponse])
async def get_characters(db: AsyncSession = Depends(get_db)):
    """获取所有角色"""
    result = await db.execute(select(Character).order_by(Character.created_at.desc()))
    characters = result.scalars().all()
    return characters


@router.post("", response_model=CharacterResponse)
async def create_character(
    character: CharacterCreate,
    db: AsyncSession = Depends(get_db),
):
    """创建角色"""
    db_character = Character(**character.model_dump())
    db.add(db_character)
    await db.commit()
    await db.refresh(db_character)
    return db_character


# 注意：这个路由必须在 /{character_id} 之前，否则会被错误匹配
@router.post("/auto-complete", response_model=CharacterAutoCompleteResponse)
async def auto_complete_character(request: CharacterAutoCompleteRequest):
    """AI 辅助填写角色设定"""
    ai_service = get_ai_service()  # 使用默认模型
    try:
        result = await ai_service.auto_complete_character(
            name=request.name,
            gender=request.gender,
            inspiration=request.inspiration,
        )
        return CharacterAutoCompleteResponse(**result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"自动补全失败: {str(e)}")


@router.get("/{character_id}", response_model=CharacterResponse)
async def get_character(character_id: str, db: AsyncSession = Depends(get_db)):
    """获取角色详情"""
    result = await db.execute(select(Character).where(Character.id == character_id))
    character = result.scalar_one_or_none()
    if not character:
        raise HTTPException(status_code=404, detail="角色不存在")
    return character


@router.put("/{character_id}", response_model=CharacterResponse)
async def update_character(
    character_id: str,
    character_update: CharacterUpdate,
    db: AsyncSession = Depends(get_db),
):
    """更新角色"""
    result = await db.execute(select(Character).where(Character.id == character_id))
    character = result.scalar_one_or_none()
    if not character:
        raise HTTPException(status_code=404, detail="角色不存在")

    update_data = character_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(character, key, value)

    await db.commit()
    await db.refresh(character)
    return character


@router.delete("/{character_id}")
async def delete_character(character_id: str, db: AsyncSession = Depends(get_db)):
    """删除角色"""
    result = await db.execute(select(Character).where(Character.id == character_id))
    character = result.scalar_one_or_none()
    if not character:
        raise HTTPException(status_code=404, detail="角色不存在")

    await db.delete(character)
    await db.commit()
    return {"message": "删除成功"}


@router.post("/{character_id}/avatar", response_model=AvatarGenerateResponse)
async def generate_avatar(
    character_id: str,
    request: AvatarGenerateRequest,
    db: AsyncSession = Depends(get_db),
):
    """生成角色头像"""
    result = await db.execute(select(Character).where(Character.id == character_id))
    character = result.scalar_one_or_none()
    if not character:
        raise HTTPException(status_code=404, detail="角色不存在")

    # 调用 AI 生成头像
    char_dict = {
        "name": character.name,
        "appearance": character.appearance,
        "personality": character.personality,
    }

    try:
        avatar_result = await ai_image_service.generate_avatar(
            character=char_dict,
            style=request.style,
            reference_image=character.base_image_url,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"头像生成失败: {str(e)}")

    # 更新角色头像
    character.avatar_url = avatar_result["avatar_url"]
    if not character.base_image_url:
        character.base_image_url = avatar_result["base_image_url"]

    await db.commit()
    await db.refresh(character)

    return avatar_result
