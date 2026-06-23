from enum import Enum


class MemoirVoiceSessionStatus(str, Enum):
    CREATED = "created"
    LISTENING = "listening"
    SPEAKING = "speaking"
    STOPPED = "stopped"
    FAILED = "failed"


class MemoirGuidanceMode(str, Enum):
    FREE_NARRATION = "free_narration"
    GUIDED_INTERVIEW = "guided_interview"


class MemoirChatMessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class MemoirChatContentStatus(str, Enum):
    PARTIAL = "partial"
    FINAL = "final"
    CORRECTED = "corrected"


class MemoirTranscriptVersionType(str, Enum):
    ASR_RAW = "asr_raw"
    DIALECT_CORRECTED = "dialect_corrected"
    USER_EDITED = "user_edited"
    FINAL = "final"


class MemoirCreatedBy(str, Enum):
    ASR = "asr"
    AI = "ai"
    USER = "user"
    SYSTEM = "system"


class MemoirOutputStatus(str, Enum):
    DRAFT = "draft"
    ACCEPTED = "accepted"
    ARCHIVED = "archived"


class MemoirBookPlanStatus(str, Enum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"


class MemoirBookChapterDraftStatus(str, Enum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"
