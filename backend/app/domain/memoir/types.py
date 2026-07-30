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


class MemoirBookRevisionProposalStatus(str, Enum):
    READY = "ready"
    APPLIED = "applied"
    DISMISSED = "dismissed"
    STALE = "stale"


class MemoirSectionType(str, Enum):
    EVENT = "event"
    LIFE_FRAGMENT = "life_fragment"
    PERSON_PORTRAIT = "person_portrait"
    LIFE_STAGE_SUMMARY = "life_stage_summary"
    VIEWPOINT_REFLECTION = "viewpoint_reflection"


class MemoirSectionStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class MemoirSectionMaterialAssignedBy(str, Enum):
    AI = "ai"
    USER = "user"
    SYSTEM = "system"


class MemoirEditingJobType(str, Enum):
    CORRECT_TRANSCRIPT = "correct_transcript"
    REPROCESS_MATERIAL_OFFLINE_ASR = "reprocess_material_offline_asr"
    GENERATE_MATERIAL_IMAGE = "generate_material_image"
    REGENERATE_CHAPTER_SECTIONS = "regenerate_chapter_sections"
    EVALUATE_INTERVIEW_POLICY_SHADOW = "evaluate_interview_policy_shadow"
    EVALUATE_INTERVIEW_POLICY_OUTCOME = "evaluate_interview_policy_outcome"
    ORGANIZE_MATERIAL = "organize_material"
    UPDATE_TOPIC_MAP = "update_topic_map"
    GENERATE_BOOK_EDITORIAL_COPY = "generate_book_editorial_copy"
    GENERATE_BOOK_REVIEW = "generate_book_review"
    APPLY_BOOK_REVIEW_ANSWERS = "apply_book_review_answers"
    PREPARE_CHAPTER = "prepare_chapter"


class MemoirEditingJobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class MemoirBookCompletionJobScope(str, Enum):
    PENDING = "pending"
    ALL = "all"


class MemoirBookCompletionJobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


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


class MemoirCompletionIssueScope(str, Enum):
    SECTION = "section"
    CHAPTER = "chapter"
    BOOK = "book"


class MemoirCompletionIssueChannel(str, Enum):
    INTERVIEW = "interview"
    MANUAL_REVIEW = "manual_review"


class MemoirCompletionIssueType(str, Enum):
    CONTEXT = "context"
    SEQUENCE = "sequence"
    RELATIONSHIP = "relationship"
    MOTIVATION = "motivation"
    IMPACT = "impact"
    MEANING = "meaning"
    CLARITY = "clarity"
    STRUCTURE = "structure"
    PRIVACY = "privacy"
    CONFLICT = "conflict"


class MemoirCompletionIssuePriority(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class MemoirCompletionIssueStatus(str, Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    CLOSED = "closed"


class MemoirCompletionIssueOutcome(str, Enum):
    ANSWERED = "answered"
    RESOLVED_BY_MATERIAL = "resolved_by_material"
    CONFIRMED = "confirmed"
    NOT_REMEMBERED = "not_remembered"
    DEFERRED = "deferred"
    DECLINED = "declined"
    SUPERSEDED = "superseded"
