from enum import Enum


class MemoirVoiceSessionStatus(str, Enum):
    CREATED = "created"
    LISTENING = "listening"
    SPEAKING = "speaking"
    STOPPED = "stopped"
    FAILED = "failed"


class MemoirInterviewType(str, Enum):
    AI_INTERVIEW = "ai_interview"
    HUMAN_INTERVIEW = "human_interview"


class MemoirSpeakerRole(str, Enum):
    NARRATOR = "narrator"
    INTERVIEWER = "interviewer"


class MemoirGuidanceMode(str, Enum):
    FREE_NARRATION = "free_narration"
    GUIDED_INTERVIEW = "guided_interview"


class MemoirSessionGoal(str, Enum):
    OPEN_NARRATION = "open_narration"
    BOOK_COMPLETION = "book_completion"


class MemoirChatMessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    INTERVIEWER = "interviewer"
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


class MemoirEditingJobType(str, Enum):
    CORRECT_TRANSCRIPT = "correct_transcript"
    REPROCESS_MATERIAL_OFFLINE_ASR = "reprocess_material_offline_asr"
    INGEST_NARRATIVE_STATE = "ingest_narrative_state"
    RECOMPUTE_CHAPTER_STRUCTURE_AUDIT = "recompute_chapter_structure_audit"
    GENERATE_FOLLOWUP_TASKS = "generate_followup_tasks"
    GENERATE_DRAFT_BLOCKS = "generate_draft_blocks"
    AUDIT_BOOK_CONSISTENCY = "audit_book_consistency"
    ROUTE_SOURCES = "route_sources"
    GENERATE_SOURCE_CARD = "generate_source_card"
    REVISE_CHAPTER = "revise_chapter"
    QUALITY_AUDIT = "quality_audit"
    GENERATE_CHAPTER_DRAFT = "generate_chapter_draft"
    GENERATE_BOOK_OUTPUT = "generate_book_output"
    GENERATE_BOOK_EDITORIAL_COPY = "generate_book_editorial_copy"


class MemoirEditingJobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class MemoirShareVideoJobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class MemoirImageAssetStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    READY = "ready"
    FAILED = "failed"
    DELETED = "deleted"


class MemoirImageAssetSourceType(str, Enum):
    SHARE_VIDEO_BACKGROUND = "share_video_background"
    CHAPTER_ILLUSTRATION = "chapter_illustration"
    UTTERANCE_ILLUSTRATION = "utterance_illustration"
    COVER_CANDIDATE = "cover_candidate"
    MANUAL_UPLOAD = "manual_upload"


class MemoirBookImagePlacementType(str, Enum):
    CHAPTER_OPENING = "chapter_opening"
    CHAPTER_INLINE = "chapter_inline"
    APPENDIX = "appendix"
    COVER = "cover"
    BACK_COVER = "back_cover"


class MemoirAutomationEventType(str, Enum):
    SOURCE_ASSIGNED = "source_assigned"
    SOURCE_HELD = "source_held"
    NARRATIVE_INGESTED = "narrative_ingested"
    FOLLOWUP_TASKS_GENERATED = "followup_tasks_generated"
    CHAPTER_AUTO_REVISED = "chapter_auto_revised"
    AUDIT_COMPLETED = "audit_completed"
    CHAPTER_DRAFT_GENERATED = "chapter_draft_generated"
    BOOK_OUTPUT_GENERATED = "book_output_generated"
    BOOK_EDITORIAL_COPY_GENERATED = "book_editorial_copy_generated"


class MemoirAutomationEventStatus(str, Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"


class MemoirSemanticCacheStatus(str, Enum):
    READY = "ready"
    STALE = "stale"
    FAILED = "failed"


class MemoirChapterSourceLinkStatus(str, Enum):
    ACTIVE = "active"
    REMOVED = "removed"


class MemoirChapterSourceAssignedBy(str, Enum):
    AI = "ai"
    USER = "user"
    SYSTEM = "system"
