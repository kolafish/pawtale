import uuid
from datetime import datetime
from sqlalchemy import String, Text, Boolean, DateTime, JSON, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
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
