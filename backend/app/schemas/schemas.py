from pydantic import BaseModel
from datetime import datetime


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


# StoryOutline Schemas
class OutlineCharacterBase(BaseModel):
    character_id: str
    role_type: str  # protagonist/deuteragonist/supporting/antagonist
    story_name: str | None = None  # 故事中的称呼
    relationships: dict = {}  # 与其他角色的关系
    story_background: str | None = None  # 故事中的特殊背景
    story_goal: str | None = None  # 故事中的目标
    sort_order: int = 0


class OutlineCharacterCreate(OutlineCharacterBase):
    pass


class OutlineCharacterUpdate(BaseModel):
    role_type: str | None = None
    story_name: str | None = None
    relationships: dict | None = None
    story_background: str | None = None
    story_goal: str | None = None
    sort_order: int | None = None


class OutlineCharacterResponse(OutlineCharacterBase):
    id: str
    outline_id: str
    character: CharacterResponse | None = None

    class Config:
        from_attributes = True


class OutlineSceneBase(BaseModel):
    scene_id: str
    usage_hint: str | None = None  # 用途提示
    is_starting_scene: bool = False  # 是否起始场景
    sort_order: int = 0


class OutlineSceneCreate(OutlineSceneBase):
    pass


class OutlineSceneUpdate(BaseModel):
    usage_hint: str | None = None
    is_starting_scene: bool | None = None
    sort_order: int | None = None


class OutlineSceneResponse(OutlineSceneBase):
    id: str
    outline_id: str
    scene: SceneResponse | None = None

    class Config:
        from_attributes = True


class StoryOutlineBase(BaseModel):
    title: str
    mood: str | None = None  # 甜/虐/搞笑/治愈
    writing_style: str | None = None  # 细腻/白描/诗意/轻松
    chapter_length: str | None = None  # 短/中/长
    central_conflict: str | None = None  # 核心冲突
    ending_type: str | None = None  # HE/BE/OE
    plot_hints: list[str] = []  # 剧情走向提示
    forbidden_elements: list[str] = []  # 禁止元素
    ai_model: str | None = "deepseek"  # AI模型选择
    # 引导流程字段
    story_goal: str | None = None  # 故事目标与读者感受
    character_tension: str | None = None  # 角色关系与核心张力
    creation_stage: str | None = "draft"  # draft/confirmed


class StoryOutlineCreate(StoryOutlineBase):
    pass


class StoryOutlineUpdate(BaseModel):
    title: str | None = None
    mood: str | None = None
    writing_style: str | None = None
    chapter_length: str | None = None
    central_conflict: str | None = None
    ending_type: str | None = None
    plot_hints: list[str] | None = None
    forbidden_elements: list[str] | None = None
    is_active: bool | None = None
    ai_model: str | None = None
    story_goal: str | None = None
    character_tension: str | None = None
    creation_stage: str | None = None


class StoryOutlineResponse(StoryOutlineBase):
    id: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    outline_characters: list[OutlineCharacterResponse] = []
    outline_scenes: list[OutlineSceneResponse] = []
    ai_model: str | None = "deepseek"
    story_goal: str | None = None
    character_tension: str | None = None
    creation_stage: str | None = "draft"

    class Config:
        from_attributes = True


class StoryOutlineGenerateRequest(BaseModel):
    """基于大纲生成故事的请求"""
    custom_direction: str | None = None  # 用户自定义开篇方向
    starting_scene_id: str | None = None  # 指定起始场景（可选，默认使用大纲中标记的起始场景）


class OutlineEnhanceRequest(BaseModel):
    """AI 增强大纲请求"""
    pass  # 不需要额外参数，使用大纲现有信息


class OutlineEnhanceResponse(BaseModel):
    """AI 增强大纲响应"""
    central_conflict: str
    plot_hints: list[str]
    suggested_mood: str | None = None
    suggested_ending: str | None = None


class OutlineTemplate(BaseModel):
    """大纲模板"""
    id: str
    name: str
    description: str
    mood: str
    writing_style: str
    central_conflict: str
    ending_type: str
    plot_hints: list[str]
    tags: list[str] = []


class OutlineTemplatesResponse(BaseModel):
    """模板列表响应"""
    templates: list[OutlineTemplate]


# Story Schemas
class PresentCharacter(BaseModel):
    """节点中出场的角色"""
    character_id: str
    name: str
    role_type: str | None = None


class SceneTransition(BaseModel):
    """场景切换信息"""
    from_scene: str | None = None
    to_scene: str | None = None
    transition_type: str | None = None  # 时间跳跃/空间转换/自然过渡


class CharacterChanges(BaseModel):
    """角色变动信息"""
    entered: list[str] = []  # 新加入角色ID
    exited: list[str] = []  # 离开角色ID


class StoryNode(BaseModel):
    title: str | None = None  # 章节标题
    content: str
    word_count: int | None = None  # 实际字数
    choices: list[dict] = []  # [{"label": "A. xxx", "next_node": "node_2"}]
    # 新增场景和角色信息
    scene_id: str | None = None  # 当前场景ID
    scene_name: str | None = None  # 场景名称
    present_characters: list[PresentCharacter] = []  # 出场角色
    scene_transition: SceneTransition | None = None  # 场景切换信息
    character_changes: CharacterChanges | None = None  # 角色变动信息


class StoryBase(BaseModel):
    title: str
    outline_id: str | None = None  # 关联大纲
    scene_id: str | None = None  # 保留用于快速创作
    mood: str | None = None
    writing_style: str | None = None  # 文字风格
    chapter_length: str | None = None  # 章节长度
    ai_model: str | None = "deepseek"  # AI模型选择
    character_ids: list[str] = []


class StoryCreate(StoryBase):
    pass


class StoryUpdate(BaseModel):
    title: str | None = None
    mood: str | None = None
    writing_style: str | None = None
    chapter_length: str | None = None
    ai_model: str | None = None


class StoryResponse(BaseModel):
    id: str
    title: str
    outline_id: str | None = None
    scene_id: str | None = None
    mood: str | None = None
    writing_style: str | None = None
    chapter_length: str | None = None
    nodes: dict
    current_node_id: str | None = None
    current_path: list[str] = []
    is_favorite: bool
    created_at: datetime
    updated_at: datetime
    characters: list[CharacterResponse] = []

    class Config:
        from_attributes = True


class StoryGenerateRequest(BaseModel):
    character_ids: list[str]
    scene_id: str
    mood: str = "甜"  # 甜/虐/搞笑/治愈
    writing_style: str = "细腻"  # 细腻/白描/诗意/轻松
    chapter_length: str = "中"  # 短/中/长
    title: str | None = None
    custom_direction: str | None = None  # 用户自定义方向
    outline_id: str | None = None  # 关联的故事大纲ID
    ai_model: str | None = None  # AI模型选择


class StoryBranchRequest(BaseModel):
    choice_index: int | None = None  # 用户选择的分支索引
    custom_direction: str | None = None  # 用户自定义方向（二选一）
    # 新增场景切换和角色管理
    switch_scene_id: str | None = None  # 切换到新场景
    add_character_ids: list[str] = []  # 添加角色
    remove_character_ids: list[str] = []  # 移除角色


class StoryGenerateResponse(BaseModel):
    story_id: str
    node_id: str
    title: str | None = None  # 章节标题
    content: str
    word_count: int | None = None
    choices: list[dict]
    # 新增场景和角色信息
    scene_id: str | None = None
    scene_name: str | None = None
    present_characters: list[PresentCharacter] = []


class StorySwitchSceneRequest(BaseModel):
    """切换场景请求"""
    scene_id: str
    transition_type: str | None = None  # 时间跳跃/空间转换/自然过渡


class StoryUpdateCharactersRequest(BaseModel):
    """更新故事角色请求"""
    add_character_ids: list[str] = []
    remove_character_ids: list[str] = []


class StoryNodeUpdateRequest(BaseModel):
    title: str | None = None
    content: str | None = None


class StoryNodeReviseRequest(BaseModel):
    instruction: str  # 修改意见


class RegenerateChoicesRequest(BaseModel):
    """重新生成分支选项请求"""
    hint: str | None = None  # 用户提示的方向


class RegenerateChoicesResponse(BaseModel):
    """重新生成分支选项响应"""
    choices: list[dict]


class PolishChaptersRequest(BaseModel):
    """润色章节请求"""
    node_ids: list[str]  # 要润色的节点 ID 列表
    instruction: str | None = None  # 用户的润色意见（可选）


class PolishChaptersResponse(BaseModel):
    """润色章节响应"""
    polished_contents: list[dict]  # [{"node_id": "...", "content": "..."}]


class StoryExportResponse(BaseModel):
    title: str
    chapters: list[dict]  # [{"title": "...", "content": "..."}]
    total_words: int


# Avatar Generation
class AvatarGenerateRequest(BaseModel):
    style: str = "日漫"  # 写实/日漫/国漫/水彩


class AvatarGenerateResponse(BaseModel):
    avatar_url: str
    base_image_url: str


# Outline Guided Creation Schemas
class OutlineGuidedCreateRequest(BaseModel):
    """引导式创建大纲请求"""
    story_goal: str  # 故事目标与读者感受
    character_tension: str  # 角色关系与核心张力
    central_conflict: str  # 剧情核心冲突
    character_ids: list[str] = []  # 已有角色ID列表
    mood: str | None = None  # 期望的情绪基调
    ai_model: str | None = "deepseek"  # 使用的AI模型


class OutlineGuidedCreateResponse(BaseModel):
    """引导式创建大纲响应"""
    title: str  # AI生成的标题建议
    mood: str  # 情绪基调
    writing_style: str  # 文字风格建议
    central_conflict: str  # 核心冲突（整理后）
    ending_type: str  # 结局类型
    plot_hints: list[str]  # 剧情提示
    forbidden_elements: list[str]  # 禁止元素
    story_goal: str  # 原始故事目标
    character_tension: str  # 原始角色张力

