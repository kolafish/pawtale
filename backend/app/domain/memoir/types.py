from enum import Enum


class MemoirSessionStatus(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"


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


class MemoirSourceArtifactType(str, Enum):
    AUDIO = "audio"
    TEXT = "text"


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


class MemoirMemoryStatus(str, Enum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class MemoirMemoryType(str, Enum):
    PERSON = "person"
    EVENT = "event"
    PLACE = "place"
    THEME = "theme"
    OPEN_QUESTION = "open_question"


class MemoirQuestionType(str, Enum):
    CLARIFICATION = "clarification"
    DEPTH = "depth"
    SCENE = "scene"
    PERSON = "person"
    EMOTION = "emotion"
    CONNECTION = "connection"
    CLOSURE = "closure"


class MemoirSensitivity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class MemoirOpenQuestionStatus(str, Enum):
    OPEN = "open"
    ASKED = "asked"
    ANSWERED = "answered"
    DISMISSED = "dismissed"


class MemoirCoverageStatus(str, Enum):
    RED = "red"
    YELLOW = "yellow"
    GREEN = "green"


class MemoirQuestionCandidateStatus(str, Enum):
    CANDIDATE = "candidate"
    SELECTED = "selected"
    ASKED = "asked"
    DISMISSED = "dismissed"


class MemoirInterviewStage(str, Enum):
    SKELETON = "skeleton"
    DEPTH = "depth"
    CONNECTION = "connection"
    POLISH = "polish"


class MemoirOutputType(str, Enum):
    SESSION_SUMMARY = "session_summary"
    STORY_FRAGMENT = "story_fragment"
    TIMELINE = "timeline"
    CONFIRMATION_TODO = "confirmation_todo"


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
