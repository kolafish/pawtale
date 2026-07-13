from __future__ import annotations

from datetime import UTC, datetime
import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.domain.memoir.narrative_types import (
    MemoirChapterContractStatus,
    MemoirChapterNodeStatus,
    MemoirChapterNodeStoryRole,
    MemoirChapterNodeType,
    MemoirNarrativeActorType,
)
from app.models.memoir_common import _enum_values


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class MemoirChapterContract(Base):
    __tablename__ = "memoir_chapter_contracts"
    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_memoir_chapter_contracts_project_id"),
        UniqueConstraint(
            "project_id",
            "chapter_id",
            "version",
            name="uq_memoir_chapter_contracts_version",
        ),
        CheckConstraint(
            f"status IN ({_enum_values(MemoirChapterContractStatus)})",
            name="ck_memoir_chapter_contracts_status",
        ),
        CheckConstraint(
            f"created_by IN ({_enum_values(MemoirNarrativeActorType)})",
            name="ck_memoir_chapter_contracts_created_by",
        ),
        CheckConstraint(
            "created_by != 'user' OR created_by_user_id IS NOT NULL",
            name="ck_memoir_chapter_contracts_created_actor",
        ),
        CheckConstraint(
            "(status = 'draft' AND confirmed_by_user_id IS NULL "
            "AND confirmed_at IS NULL AND superseded_at IS NULL) OR "
            "(status = 'confirmed' AND confirmed_by_user_id IS NOT NULL "
            "AND confirmed_at IS NOT NULL AND superseded_at IS NULL) OR "
            "(status = 'superseded' AND superseded_at IS NOT NULL "
            "AND ((confirmed_by_user_id IS NULL AND confirmed_at IS NULL) "
            "OR (confirmed_by_user_id IS NOT NULL AND confirmed_at IS NOT NULL)))",
            name="ck_memoir_chapter_contracts_lifecycle_shape",
        ),
        CheckConstraint("version >= 1", name="ck_memoir_chapter_contracts_version"),
        CheckConstraint("length(trim(objective)) > 0", name="ck_memoir_chapter_contracts_objective"),
        CheckConstraint(
            "length(trim(reader_takeaway)) > 0",
            name="ck_memoir_chapter_contracts_takeaway",
        ),
        CheckConstraint(
            "(time_start IS NULL AND time_end IS NULL) OR "
            "(time_start IS NOT NULL AND time_end IS NOT NULL AND time_end >= time_start)",
            name="ck_memoir_chapter_contracts_time_range",
        ),
        Index(
            "uq_memoir_chapter_contracts_current_draft",
            "project_id",
            "chapter_id",
            unique=True,
            sqlite_where=text("status = 'draft'"),
            postgresql_where=text("status = 'draft'"),
        ),
        Index(
            "uq_memoir_chapter_contracts_current_confirmed",
            "project_id",
            "chapter_id",
            unique=True,
            sqlite_where=text("status = 'confirmed'"),
            postgresql_where=text("status = 'confirmed'"),
        ),
        Index(
            "ix_memoir_chapter_contracts_project_chapter",
            "project_id",
            "chapter_id",
            "version",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    chapter_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("memoir_book_plan_chapters.id"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    reader_takeaway: Mapped[str] = mapped_column(Text, nullable=False)
    time_scope_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    time_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    time_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    core_questions_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    constraints_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_by: Mapped[str] = mapped_column(String(12), nullable=False)
    created_by_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True
    )
    confirmed_by_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )

    nodes: Mapped[list["MemoirChapterNode"]] = relationship(
        "MemoirChapterNode",
        back_populates="contract",
        cascade="all, delete-orphan",
        order_by="MemoirChapterNode.sort_order",
    )


class MemoirChapterNode(Base):
    __tablename__ = "memoir_chapter_nodes"
    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_memoir_chapter_nodes_project_id"),
        UniqueConstraint("contract_id", "id", name="uq_memoir_chapter_nodes_contract_id"),
        UniqueConstraint(
            "project_id",
            "contract_id",
            "sort_order",
            name="uq_memoir_chapter_nodes_contract_order",
        ),
        ForeignKeyConstraint(
            ["project_id", "contract_id"],
            ["memoir_chapter_contracts.project_id", "memoir_chapter_contracts.id"],
            ondelete="CASCADE",
            name="fk_memoir_chapter_nodes_contract_project",
        ),
        ForeignKeyConstraint(
            ["project_id", "transition_from_node_id"],
            ["memoir_chapter_nodes.project_id", "memoir_chapter_nodes.id"],
            name="fk_memoir_chapter_nodes_transition_from_project",
        ),
        ForeignKeyConstraint(
            ["project_id", "transition_to_node_id"],
            ["memoir_chapter_nodes.project_id", "memoir_chapter_nodes.id"],
            name="fk_memoir_chapter_nodes_transition_to_project",
        ),
        CheckConstraint(
            f"node_type IN ({_enum_values(MemoirChapterNodeType)})",
            name="ck_memoir_chapter_nodes_type",
        ),
        CheckConstraint(
            f"status IN ({_enum_values(MemoirChapterNodeStatus)})",
            name="ck_memoir_chapter_nodes_status",
        ),
        CheckConstraint(
            f"created_by IN ({_enum_values(MemoirNarrativeActorType)})",
            name="ck_memoir_chapter_nodes_created_by",
        ),
        CheckConstraint(
            f"updated_by IN ({_enum_values(MemoirNarrativeActorType)})",
            name="ck_memoir_chapter_nodes_updated_by",
        ),
        CheckConstraint(
            "created_by != 'user' OR created_by_user_id IS NOT NULL",
            name="ck_memoir_chapter_nodes_created_actor",
        ),
        CheckConstraint(
            "updated_by != 'user' OR updated_by_user_id IS NOT NULL",
            name="ck_memoir_chapter_nodes_updated_actor",
        ),
        CheckConstraint("sort_order >= 0", name="ck_memoir_chapter_nodes_sort_order"),
        CheckConstraint("length(trim(title)) > 0", name="ck_memoir_chapter_nodes_title"),
        CheckConstraint("length(trim(purpose)) > 0", name="ck_memoir_chapter_nodes_purpose"),
        CheckConstraint(
            "(node_type = 'transition' AND transition_from_node_id IS NOT NULL "
            "AND transition_to_node_id IS NOT NULL "
            "AND transition_from_node_id != transition_to_node_id) OR "
            "(node_type != 'transition' AND transition_from_node_id IS NULL "
            "AND transition_to_node_id IS NULL)",
            name="ck_memoir_chapter_nodes_transition_shape",
        ),
        Index(
            "ix_memoir_chapter_nodes_project_chapter",
            "project_id",
            "chapter_id",
            "contract_id",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    chapter_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("memoir_book_plan_chapters.id"), nullable=False
    )
    contract_id: Mapped[str] = mapped_column(String(36), nullable=False)
    node_type: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    transition_from_node_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True
    )
    transition_to_node_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_by: Mapped[str] = mapped_column(String(12), nullable=False)
    created_by_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True
    )
    updated_by: Mapped[str] = mapped_column(String(12), nullable=False)
    updated_by_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )

    contract: Mapped[MemoirChapterContract] = relationship(
        "MemoirChapterContract",
        back_populates="nodes",
        foreign_keys=[contract_id],
    )
    stories: Mapped[list["MemoirChapterNodeStory"]] = relationship(
        "MemoirChapterNodeStory",
        back_populates="node",
        cascade="all, delete-orphan",
        order_by="MemoirChapterNodeStory.sort_order",
    )


class MemoirChapterNodeStory(Base):
    __tablename__ = "memoir_chapter_node_stories"
    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_memoir_chapter_node_stories_project_id"),
        UniqueConstraint(
            "project_id",
            "node_id",
            "story_id",
            name="uq_memoir_chapter_node_stories_node_story",
        ),
        ForeignKeyConstraint(
            ["project_id", "node_id"],
            ["memoir_chapter_nodes.project_id", "memoir_chapter_nodes.id"],
            ondelete="CASCADE",
            name="fk_memoir_chapter_node_stories_node_project",
        ),
        ForeignKeyConstraint(
            ["project_id", "story_id"],
            ["memoir_stories.project_id", "memoir_stories.id"],
            name="fk_memoir_chapter_node_stories_story_project",
        ),
        CheckConstraint(
            f"story_role IN ({_enum_values(MemoirChapterNodeStoryRole)})",
            name="ck_memoir_chapter_node_stories_role",
        ),
        CheckConstraint(
            f"created_by IN ({_enum_values(MemoirNarrativeActorType)})",
            name="ck_memoir_chapter_node_stories_created_by",
        ),
        CheckConstraint(
            "created_by != 'user' OR created_by_user_id IS NOT NULL",
            name="ck_memoir_chapter_node_stories_created_actor",
        ),
        CheckConstraint("sort_order >= 0", name="ck_memoir_chapter_node_stories_sort_order"),
        Index("ix_memoir_chapter_node_stories_project_node", "project_id", "node_id"),
        Index("ix_memoir_chapter_node_stories_project_story", "project_id", "story_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    node_id: Mapped[str] = mapped_column(String(36), nullable=False)
    story_id: Mapped[str] = mapped_column(String(36), nullable=False)
    story_role: Mapped[str] = mapped_column(String(24), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_by: Mapped[str] = mapped_column(String(12), nullable=False)
    created_by_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    node: Mapped[MemoirChapterNode] = relationship(
        "MemoirChapterNode",
        back_populates="stories",
        foreign_keys=[node_id],
    )
