from __future__ import annotations

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.clock import utcnow_naive
from app.database import Base
from app.domain.memoir.types import MemoirRetrievalEntryKind
from app.models.memoir_common import _enum_values


class MemoirRetrievalEntry(Base):
    __tablename__ = "memoir_retrieval_entries"
    __table_args__ = (
        UniqueConstraint(
            "utterance_id",
            "entry_kind",
            "ordinal",
            name="uq_memoir_retrieval_entries_source_slot",
        ),
        CheckConstraint(
            f"entry_kind IN ({_enum_values(MemoirRetrievalEntryKind)})",
            name="ck_memoir_retrieval_entries_kind",
        ),
        CheckConstraint(
            "text_source IN ('raw', 'edited')",
            name="ck_memoir_retrieval_entries_text_source",
        ),
        CheckConstraint(
            "ordinal >= 0",
            name="ck_memoir_retrieval_entries_nonnegative_ordinal",
        ),
        CheckConstraint(
            "index_version > 0",
            name="ck_memoir_retrieval_entries_positive_index_version",
        ),
        Index(
            "ix_memoir_retrieval_entries_scope",
            "project_id",
            "voice_session_id",
            "entry_kind",
        ),
        Index(
            "ix_memoir_retrieval_entries_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        Identity(always=True),
        primary_key=True,
    )
    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    voice_session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("memoir_voice_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    utterance_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("memoir_voice_utterances.id", ondelete="CASCADE"),
        nullable=False,
    )
    transcript_version_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("memoir_transcript_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    audio_file_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("audio_files.id", ondelete="SET NULL"),
        nullable=True,
    )
    entry_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    search_text: Mapped[str] = mapped_column(Text, nullable=False)
    text_source: Mapped[str] = mapped_column(String(20), nullable=False)
    source_text_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(512), nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(120), nullable=False)
    index_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utcnow_naive,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utcnow_naive,
        onupdate=utcnow_naive,
        nullable=False,
    )
