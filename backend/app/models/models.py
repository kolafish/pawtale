import uuid
from datetime import datetime
from sqlalchemy import String, Text, Boolean, DateTime, JSON, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class Character(Base):
    __tablename__ = "characters"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    gender: Mapped[str | None] = mapped_column(String(10), nullable=True)  # 男/女/其他
    age: Mapped[int | None] = mapped_column(nullable=True)
    identity: Mapped[str | None] = mapped_column(String(200), nullable=True)
    appearance: Mapped[str | None] = mapped_column(Text, nullable=True)
    personality: Mapped[list] = mapped_column(JSON, default=list)  # 性格标签列表
    speaking_style: Mapped[str | None] = mapped_column(Text, nullable=True)
    background: Mapped[str | None] = mapped_column(Text, nullable=True)
    inspiration: Mapped[str | None] = mapped_column(Text, nullable=True)  # 灵感来源
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    base_image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_preset: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # 新增深度字段
    role_type: Mapped[str | None] = mapped_column(String(20), nullable=True)  # protagonist/supporting/antagonist
    core_trait: Mapped[str | None] = mapped_column(Text, nullable=True)  # 底色 - 核心性格驱动力
    life_goal: Mapped[str | None] = mapped_column(Text, nullable=True)  # 目标追求 - 人生核心追求
    fatal_flaw: Mapped[str | None] = mapped_column(Text, nullable=True)  # 致命弱点
    growth_hint: Mapped[str | None] = mapped_column(Text, nullable=True)  # 成长弧线提示
    habits: Mapped[list] = mapped_column(JSON, default=list)  # 行为习惯
    secret: Mapped[str | None] = mapped_column(Text, nullable=True)  # 秘密 - 可作为剧情转折点

    # 关联
    stories: Mapped[list["Story"]] = relationship(
        "Story",
        secondary="story_characters",
        back_populates="characters"
    )


class Scene(Base):
    __tablename__ = "scenes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False)  # 初遇/暧昧/表白/甜蜜/虐心
    description: Mapped[str] = mapped_column(Text, nullable=False)
    location: Mapped[str | None] = mapped_column(String(200), nullable=True)
    time_weather: Mapped[str | None] = mapped_column(String(100), nullable=True)
    mood_keywords: Mapped[list] = mapped_column(JSON, default=list)
    is_preset: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # 新增增强字段
    dramatic_potential: Mapped[str | None] = mapped_column(Text, nullable=True)  # 戏剧潜力 - 适合什么类型剧情
    sensory_details: Mapped[dict] = mapped_column(JSON, default=dict)  # 感官细节 {visual, auditory, olfactory}
    props: Mapped[list] = mapped_column(JSON, default=list)  # 可能的道具/元素


class StoryOutline(Base):
    """故事大纲 - 管理整体故事配置"""
    __tablename__ = "story_outlines"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)  # 大纲标题
    mood: Mapped[str | None] = mapped_column(String(50), nullable=True)  # 甜/虐/搞笑/治愈
    writing_style: Mapped[str | None] = mapped_column(String(50), nullable=True)  # 细腻/白描/诗意/轻松
    chapter_length: Mapped[str | None] = mapped_column(String(20), nullable=True)  # 短/中/长
    central_conflict: Mapped[str | None] = mapped_column(Text, nullable=True)  # 核心冲突
    ending_type: Mapped[str | None] = mapped_column(String(20), nullable=True)  # HE/BE/OE
    plot_hints: Mapped[list] = mapped_column(JSON, default=list)  # 剧情走向提示
    forbidden_elements: Mapped[list] = mapped_column(JSON, default=list)  # 禁止元素

    # AI模型选择
    ai_model: Mapped[str | None] = mapped_column(String(50), nullable=True, default="claude-opus-4")  # deepseek/claude-opus-4/claude-sonnet-4

    # 引导流程字段
    story_goal: Mapped[str | None] = mapped_column(Text, nullable=True)  # 故事目标与读者感受
    character_tension: Mapped[str | None] = mapped_column(Text, nullable=True)  # 角色关系与核心张力
    creation_stage: Mapped[str | None] = mapped_column(String(20), nullable=True, default="draft")  # draft/confirmed

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # 关联
    outline_characters: Mapped[list["OutlineCharacter"]] = relationship(
        "OutlineCharacter",
        back_populates="outline",
        cascade="all, delete-orphan"
    )
    outline_scenes: Mapped[list["OutlineScene"]] = relationship(
        "OutlineScene",
        back_populates="outline",
        cascade="all, delete-orphan"
    )
    stories: Mapped[list["Story"]] = relationship("Story", back_populates="outline")


class OutlineCharacter(Base):
    """大纲角色配置 - 角色在特定大纲中的配置"""
    __tablename__ = "outline_characters"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    outline_id: Mapped[str] = mapped_column(String(36), ForeignKey("story_outlines.id"), nullable=False)
    character_id: Mapped[str] = mapped_column(String(36), ForeignKey("characters.id"), nullable=False)
    role_type: Mapped[str] = mapped_column(String(20), nullable=False)  # protagonist/deuteragonist/supporting/antagonist
    story_name: Mapped[str | None] = mapped_column(String(100), nullable=True)  # 故事中的称呼
    relationships: Mapped[dict] = mapped_column(JSON, default=dict)  # 与其他角色的关系
    story_background: Mapped[str | None] = mapped_column(Text, nullable=True)  # 故事中的特殊背景
    story_goal: Mapped[str | None] = mapped_column(Text, nullable=True)  # 故事中的目标
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    # 关联
    outline: Mapped["StoryOutline"] = relationship("StoryOutline", back_populates="outline_characters")
    character: Mapped["Character"] = relationship("Character")


class OutlineScene(Base):
    """大纲场景配置 - 场景在特定大纲中的配置"""
    __tablename__ = "outline_scenes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    outline_id: Mapped[str] = mapped_column(String(36), ForeignKey("story_outlines.id"), nullable=False)
    scene_id: Mapped[str] = mapped_column(String(36), ForeignKey("scenes.id"), nullable=False)
    usage_hint: Mapped[str | None] = mapped_column(Text, nullable=True)  # 用途提示
    is_starting_scene: Mapped[bool] = mapped_column(Boolean, default=False)  # 是否起始场景
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    # 关联
    outline: Mapped["StoryOutline"] = relationship("StoryOutline", back_populates="outline_scenes")
    scene: Mapped["Scene"] = relationship("Scene")


class Story(Base):
    __tablename__ = "stories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    outline_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("story_outlines.id"), nullable=True)  # 关联大纲
    scene_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("scenes.id"), nullable=True)  # 保留用于快速创作
    mood: Mapped[str | None] = mapped_column(String(50), nullable=True)  # 甜/虐/搞笑/治愈
    writing_style: Mapped[str | None] = mapped_column(String(50), nullable=True)  # 文字风格: 细腻/白描/诗意/轻松
    chapter_length: Mapped[str | None] = mapped_column(String(20), nullable=True)  # 章节长度: 短/中/长
    ai_model: Mapped[str | None] = mapped_column(String(50), nullable=True, default="claude-opus-4")  # AI模型选择
    nodes: Mapped[dict] = mapped_column(JSON, default=dict)  # 剧情节点树（增强结构）
    current_node_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    current_path: Mapped[list] = mapped_column(JSON, default=list)  # 当前阅读路径
    is_favorite: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # 关联
    outline: Mapped[StoryOutline | None] = relationship("StoryOutline", back_populates="stories")
    scene: Mapped[Scene | None] = relationship("Scene")
    characters: Mapped[list[Character]] = relationship(
        "Character",
        secondary="story_characters",
        back_populates="stories"
    )
    workspace: Mapped["StoryWorkspace | None"] = relationship(
        "StoryWorkspace",
        back_populates="story",
        uselist=False,
        cascade="all, delete-orphan",
    )


class StoryCharacter(Base):
    """故事-角色关联表"""
    __tablename__ = "story_characters"

    story_id: Mapped[str] = mapped_column(String(36), ForeignKey("stories.id"), primary_key=True)
    character_id: Mapped[str] = mapped_column(String(36), ForeignKey("characters.id"), primary_key=True)


class StoryWorkspace(Base):
    """编辑器优先的故事工作区"""
    __tablename__ = "story_workspaces"

    story_id: Mapped[str] = mapped_column(String(36), ForeignKey("stories.id"), primary_key=True)
    editor_content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    chapters: Mapped[list] = mapped_column(JSON, default=list)
    extracted_characters: Mapped[list] = mapped_column(JSON, default=list)
    extracted_scenes: Mapped[list] = mapped_column(JSON, default=list)
    extracted_outline_points: Mapped[list] = mapped_column(JSON, default=list)
    extraction_notes: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    story: Mapped["Story"] = relationship("Story", back_populates="workspace")
