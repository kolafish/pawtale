import uuid
from datetime import datetime, date
from sqlalchemy import String, Text, Integer, Date, DateTime, JSON, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    word_count_target: Mapped[int | None] = mapped_column(Integer, nullable=True)
    daily_word_goal: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    groups: Mapped[list["DocumentGroup"]] = relationship(
        "DocumentGroup", back_populates="project", cascade="all, delete-orphan",
        order_by="DocumentGroup.sort_order",
    )
    references: Mapped[list["ReferenceItem"]] = relationship(
        "ReferenceItem", back_populates="project", cascade="all, delete-orphan",
    )
    sessions: Mapped[list["WritingSession"]] = relationship(
        "WritingSession", back_populates="project", cascade="all, delete-orphan",
    )


class DocumentGroup(Base):
    __tablename__ = "document_groups"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    project: Mapped["Project"] = relationship("Project", back_populates="groups")
    documents: Mapped[list["Document"]] = relationship(
        "Document", back_populates="group", cascade="all, delete-orphan",
        order_by="Document.sort_order",
    )


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    group_id: Mapped[str] = mapped_column(String(36), ForeignKey("document_groups.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    synopsis: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft/revision/done
    label: Mapped[str | None] = mapped_column(String(50), nullable=True)
    word_count_target: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    group: Mapped["DocumentGroup"] = relationship("DocumentGroup", back_populates="documents")
    reference_links: Mapped[list["DocumentReference"]] = relationship(
        "DocumentReference", back_populates="document", cascade="all, delete-orphan",
    )


class ReferenceItem(Base):
    __tablename__ = "reference_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"), nullable=False)
    type: Mapped[str] = mapped_column(String(20), nullable=False)  # character/location/note
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    project: Mapped["Project"] = relationship("Project", back_populates="references")
    document_links: Mapped[list["DocumentReference"]] = relationship(
        "DocumentReference", back_populates="reference", cascade="all, delete-orphan",
    )


class DocumentReference(Base):
    __tablename__ = "document_references"

    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("documents.id"), primary_key=True,
    )
    reference_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("reference_items.id"), primary_key=True,
    )

    document: Mapped["Document"] = relationship("Document", back_populates="reference_links")
    reference: Mapped["ReferenceItem"] = relationship("ReferenceItem", back_populates="document_links")


class WritingSession(Base):
    __tablename__ = "writing_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    words_written: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    project: Mapped["Project"] = relationship("Project", back_populates="sessions")
