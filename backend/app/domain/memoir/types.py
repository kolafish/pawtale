from enum import Enum


class MemoirSessionStatus(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"


class MemoirQuestionSource(str, Enum):
    TEMPLATE = "template"
    AI_GENERATED = "ai_generated"


class MemoirQuestionStatus(str, Enum):
    PENDING = "pending"
    ANSWERED = "answered"
    SKIPPED = "skipped"


class MemoirAnswerStatus(str, Enum):
    UPLOADED = "uploaded"
    TRANSCRIBING = "transcribing"
    ANSWERED = "answered"
    DRAFTED = "drafted"
    CONFIRMED = "confirmed"


class MemoirDraftStatus(str, Enum):
    DRAFT = "draft"
    ACCEPTED = "accepted"
