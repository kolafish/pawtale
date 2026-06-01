from pydantic import BaseModel
from datetime import datetime


# AI Debug Schemas
class AIDebugRequestInfo(BaseModel):
    provider: str
    model: str
    temperature: float | None = None
    max_tokens: int | None = None
    system_prompt: str | None = None
    user_prompt: str | None = None


class AIDebugResponseInfo(BaseModel):
    raw_text: str | None = None
    raw_json: dict | list | str | None = None


class AIDebugMetaInfo(BaseModel):
    truncated: bool = False
    max_chars: int | None = None
    generated_at: str | None = None


class AIDebugInfo(BaseModel):
    request: AIDebugRequestInfo
    response: AIDebugResponseInfo
    meta: AIDebugMetaInfo


# Character Schemas
class CharacterBase(BaseModel):
    name: str
    gender: str | None = None  # 男/女/其他
    age: int | None = None
    identity: str | None = None
    appearance: str | None = None
    personality: list[str] = []
    speaking_style: str | None = None
    background: str | None = None
    inspiration: str | None = None
    # 新增深度字段
    role_type: str | None = None  # protagonist/supporting/antagonist
    core_trait: str | None = None  # 底色 - 核心性格驱动力
    life_goal: str | None = None  # 目标追求
    fatal_flaw: str | None = None  # 致命弱点
    growth_hint: str | None = None  # 成长弧线提示
    habits: list[str] = []  # 行为习惯
    secret: str | None = None  # 秘密


class CharacterCreate(CharacterBase):
    pass


class CharacterUpdate(BaseModel):
    name: str | None = None
    gender: str | None = None
    age: int | None = None
    identity: str | None = None
    appearance: str | None = None
    personality: list[str] | None = None
    speaking_style: str | None = None
    background: str | None = None
    inspiration: str | None = None
    # 新增深度字段
    role_type: str | None = None
    core_trait: str | None = None
    life_goal: str | None = None
    fatal_flaw: str | None = None
    growth_hint: str | None = None
    habits: list[str] | None = None
    secret: str | None = None


class CharacterResponse(CharacterBase):
    id: str
    avatar_url: str | None = None
    base_image_url: str | None = None
    is_preset: bool
    created_at: datetime

    class Config:
        from_attributes = True


class CharacterAutoCompleteRequest(BaseModel):
    name: str | None = None
    gender: str | None = None
    inspiration: str | None = None  # 灵感来源，如 "何以琛"
    ai_model: str | None = None  # deepseek/claude-opus-4/claude-sonnet-4


class CharacterAutoCompleteResponse(BaseModel):
    gender: str | None = None
    age: int | None = None
    identity: str | None = None
    appearance: str | None = None
    personality: list[str] = []
    speaking_style: str | None = None
    background: str | None = None
    # 新增深度字段
    core_trait: str | None = None
    life_goal: str | None = None
    fatal_flaw: str | None = None
    growth_hint: str | None = None
    habits: list[str] = []
    secret: str | None = None


# Scene Schemas
class SceneBase(BaseModel):
    name: str
    category: str
    description: str
    location: str | None = None
    time_weather: str | None = None
    mood_keywords: list[str] = []
    # 新增增强字段
    dramatic_potential: str | None = None  # 戏剧潜力
    sensory_details: dict = {}  # 感官细节 {visual, auditory, olfactory}
    props: list[str] = []  # 可能的道具/元素


class SceneCreate(SceneBase):
    pass


class SceneUpdate(BaseModel):
    name: str | None = None
    category: str | None = None
    description: str | None = None
    location: str | None = None
    time_weather: str | None = None
    mood_keywords: list[str] | None = None
    # 新增增强字段
    dramatic_potential: str | None = None
    sensory_details: dict | None = None
    props: list[str] | None = None


class SceneResponse(SceneBase):
    id: str
    is_preset: bool
    created_at: datetime

    class Config:
        from_attributes = True


# AI Scene Generation Schemas
class SceneAIGenerateRequest(BaseModel):
    category: str  # 初遇/暧昧/表白/甜蜜/虐心
    mood_keywords: list[str] = []  # 氛围关键词
    character_context: dict | None = None  # {"character_ids": [...], "relationship": "陌生人"}
    custom_prompt: str | None = None  # 用户自定义提示
    count: int = 3  # 生成候选数量
    ai_model: str | None = None  # deepseek/claude-opus-4/claude-sonnet-4
    debug: bool = False


class SceneCandidate(BaseModel):
    name: str
    description: str
    location: str | None = None
    time_weather: str | None = None
    mood_keywords: list[str] = []
    dramatic_potential: str | None = None
    sensory_details: dict = {}
    props: list[str] = []


class SceneAIGenerateResponse(BaseModel):
    candidates: list[SceneCandidate]
    debug_info: AIDebugInfo | None = None


# Avatar Generation
class AvatarGenerateRequest(BaseModel):
    style: str = "日漫"  # 写实/日漫/国漫/水彩


class AvatarGenerateResponse(BaseModel):
    avatar_url: str
    base_image_url: str
