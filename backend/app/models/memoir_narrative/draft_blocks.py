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
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.domain.memoir.narrative_types import (
    MemoirDraftBlockClaimUsageRole,
    MemoirDraftBlockEditorialStatus,
    MemoirDraftBlockFreshnessStatus,
    MemoirDraftBlockRole,
    MemoirDraftBlockStoryUsageRole,
    MemoirNarrativeActorType,
)
from app.models.memoir_common import _enum_values


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class MemoirDraftBlock(Base):
    __tablename__ = "memoir_draft_blocks"
    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_memoir_draft_blocks_project_id"),
        UniqueConstraint("legacy_draft_id", name="uq_memoir_draft_blocks_legacy_draft"),
        ForeignKeyConstraint(
            ["project_id", "node_id"],
            ["memoir_chapter_nodes.project_id", "memoir_chapter_nodes.id"],
            name="fk_memoir_draft_blocks_node_project",
        ),
        CheckConstraint(
            f"block_role IN ({_enum_values(MemoirDraftBlockRole)})",
            name="ck_memoir_draft_blocks_role",
        ),
        CheckConstraint(
            f"editorial_status IN ({_enum_values(MemoirDraftBlockEditorialStatus)})",
            name="ck_memoir_draft_blocks_editorial_status",
        ),
        CheckConstraint(
            f"freshness_status IN ({_enum_values(MemoirDraftBlockFreshnessStatus)})",
            name="ck_memoir_draft_blocks_freshness_status",
        ),
        CheckConstraint("sort_order >= 0", name="ck_memoir_draft_blocks_sort_order"),
        CheckConstraint(
            "(block_role = 'legacy_snapshot' AND node_id IS NULL AND legacy_draft_id IS NOT NULL) OR "
            "(block_role != 'legacy_snapshot' AND node_id IS NOT NULL AND legacy_draft_id IS NULL)",
            name="ck_memoir_draft_blocks_source_shape",
        ),
        Index(
            "ix_memoir_draft_blocks_project_chapter",
            "project_id",
            "chapter_id",
            "sort_order",
        ),
        Index(
            "ix_memoir_draft_blocks_project_freshness",
            "project_id",
            "freshness_status",
        ),
        Index(
            "uq_memoir_draft_blocks_structured_chapter_order",
            "project_id",
            "chapter_id",
            "sort_order",
            unique=True,
            sqlite_where=text("block_role != 'legacy_snapshot'"),
            postgresql_where=text("block_role != 'legacy_snapshot'"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    chapter_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("memoir_book_plan_chapters.id", ondelete="CASCADE"), nullable=False
    )
    node_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    block_role: Mapped[str] = mapped_column(String(24), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    editorial_status: Mapped[str] = mapped_column(String(16), nullable=False)
    freshness_status: Mapped[str] = mapped_column(String(16), nullable=False)
    locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    current_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    legacy_draft_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )

    versions: Mapped[list["MemoirDraftBlockVersion"]] = relationship(
        "MemoirDraftBlockVersion",
        back_populates="block",
        cascade="all, delete-orphan",
        foreign_keys="MemoirDraftBlockVersion.block_id",
        order_by="MemoirDraftBlockVersion.version",
    )


class MemoirDraftBlockVersion(Base):
    __tablename__ = "memoir_draft_block_versions"
    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_memoir_draft_block_versions_project_id"),
        UniqueConstraint(
            "project_id",
            "block_id",
            "id",
            name="uq_memoir_draft_block_versions_block_id",
        ),
        UniqueConstraint(
            "project_id",
            "block_id",
            "version",
            name="uq_memoir_draft_block_versions_version",
        ),
        UniqueConstraint(
            "legacy_draft_version_id",
            name="uq_memoir_draft_block_versions_legacy_version",
        ),
        ForeignKeyConstraint(
            ["project_id", "block_id"],
            ["memoir_draft_blocks.project_id", "memoir_draft_blocks.id"],
            ondelete="CASCADE",
            name="fk_memoir_draft_block_versions_block_project",
        ),
        CheckConstraint("version >= 1", name="ck_memoir_draft_block_versions_version"),
        CheckConstraint(
            f"created_by IN ({_enum_values(MemoirNarrativeActorType)})",
            name="ck_memoir_draft_block_versions_created_by",
        ),
        Index(
            "ix_memoir_draft_block_versions_project_block",
            "project_id",
            "block_id",
            "version",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    block_id: Mapped[str] = mapped_column(String(36), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str] = mapped_column(String(12), nullable=False)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    prompt_snapshot: Mapped[str] = mapped_column(Text, default="", nullable=False)
    feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    legacy_draft_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    block: Mapped[MemoirDraftBlock] = relationship(
        "MemoirDraftBlock",
        back_populates="versions",
        foreign_keys=[block_id],
    )
    claims: Mapped[list["MemoirDraftBlockClaim"]] = relationship(
        "MemoirDraftBlockClaim",
        cascade="all, delete-orphan",
        order_by="MemoirDraftBlockClaim.claim_id",
    )
    stories: Mapped[list["MemoirDraftBlockStory"]] = relationship(
        "MemoirDraftBlockStory",
        cascade="all, delete-orphan",
        order_by="MemoirDraftBlockStory.story_id",
    )


class MemoirDraftBlockClaim(Base):
    __tablename__ = "memoir_draft_block_claims"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "block_version_id"],
            ["memoir_draft_block_versions.project_id", "memoir_draft_block_versions.id"],
            ondelete="CASCADE",
            name="fk_memoir_draft_block_claims_version_project",
        ),
        ForeignKeyConstraint(
            ["project_id", "claim_id"],
            ["memoir_claims.project_id", "memoir_claims.id"],
            name="fk_memoir_draft_block_claims_claim_project",
        ),
        CheckConstraint(
            f"usage_role IN ({_enum_values(MemoirDraftBlockClaimUsageRole)})",
            name="ck_memoir_draft_block_claims_usage_role",
        ),
        Index(
            "ix_memoir_draft_block_claims_project_claim",
            "project_id",
            "claim_id",
            "block_version_id",
        ),
    )

    block_version_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    claim_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    usage_role: Mapped[str] = mapped_column(String(16), nullable=False)


class MemoirDraftBlockStory(Base):
    __tablename__ = "memoir_draft_block_stories"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "block_version_id"],
            ["memoir_draft_block_versions.project_id", "memoir_draft_block_versions.id"],
            ondelete="CASCADE",
            name="fk_memoir_draft_block_stories_version_project",
        ),
        ForeignKeyConstraint(
            ["project_id", "story_id"],
            ["memoir_stories.project_id", "memoir_stories.id"],
            name="fk_memoir_draft_block_stories_story_project",
        ),
        ForeignKeyConstraint(
            ["project_id", "story_id", "story_revision_id"],
            [
                "memoir_story_revisions.project_id",
                "memoir_story_revisions.story_id",
                "memoir_story_revisions.id",
            ],
            name="fk_memoir_draft_block_stories_revision_story",
        ),
        CheckConstraint(
            f"usage_role IN ({_enum_values(MemoirDraftBlockStoryUsageRole)})",
            name="ck_memoir_draft_block_stories_usage_role",
        ),
        Index(
            "ix_memoir_draft_block_stories_project_story",
            "project_id",
            "story_id",
            "story_revision_id",
        ),
    )

    block_version_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    story_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    story_revision_id: Mapped[str] = mapped_column(String(36), nullable=False)
    usage_role: Mapped[str] = mapped_column(String(16), nullable=False)
