from enum import Enum


class MemoirNarrativeActorType(str, Enum):
    AI = "ai"
    USER = "user"
    SYSTEM = "system"


class MemoirNarrativeEntityType(str, Enum):
    PERSON = "person"
    PLACE = "place"
    ORGANIZATION = "organization"


class MemoirNarrativeEntityVerificationStatus(str, Enum):
    UNREVIEWED = "unreviewed"
    CONFIRMED = "confirmed"
    NEEDS_VERIFICATION = "needs_verification"
    REJECTED = "rejected"


class MemoirClaimType(str, Enum):
    EVENT = "event"
    RELATIONSHIP = "relationship"
    ATTRIBUTE = "attribute"
    CHRONOLOGY = "chronology"
    REFLECTION = "reflection"


class MemoirClaimPredicate(str, Enum):
    NARRATIVE_FACT = "narrative_fact"
    FAMILY_RELATIONSHIP = "family_relationship"
    SOCIAL_RELATIONSHIP = "social_relationship"
    IDENTITY_ATTRIBUTE = "identity_attribute"
    OCCURRED_AT = "occurred_at"
    BEFORE = "before"
    AFTER = "after"


class MemoirClaimTimePrecision(str, Enum):
    EXACT = "exact"
    DAY = "day"
    MONTH = "month"
    YEAR = "year"
    PERIOD = "period"
    RELATIVE = "relative"
    UNKNOWN = "unknown"


class MemoirClaimCertainty(str, Enum):
    EXACT = "exact"
    APPROXIMATE = "approximate"
    UNCERTAIN = "uncertain"


class MemoirClaimVerificationStatus(str, Enum):
    UNREVIEWED = "unreviewed"
    CONFIRMED = "confirmed"
    NEEDS_VERIFICATION = "needs_verification"
    DISPUTED = "disputed"
    REJECTED = "rejected"


class MemoirClaimPublicationStatus(str, Enum):
    ELIGIBLE = "eligible"
    PRIVATE = "private"
    EXCLUDED = "excluded"


class MemoirClaimEvidenceRole(str, Enum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"


class MemoirClaimEvidenceSourceType(str, Enum):
    TRANSCRIPT = "transcript"
    IMAGE = "image"
    MANUAL = "manual"


class MemoirClaimEvidenceSpeakerRole(str, Enum):
    NARRATOR = "narrator"
    INTERVIEWER = "interviewer"
    OTHER = "other"


class MemoirNarrativeDecisionTargetType(str, Enum):
    ENTITY = "entity"
    CLAIM = "claim"
    STORY = "story"
    STORY_SLOT = "story_slot"
    GAP = "gap"
    FOLLOWUP_TASK = "followup_task"
    CHAPTER_CONTRACT = "chapter_contract"
    DRAFT_BLOCK = "draft_block"


class MemoirNarrativeDecisionAction(str, Enum):
    CONFIRM = "confirm"
    INCLUDE = "include"
    REJECT = "reject"
    MERGE = "merge"
    RESOLVE = "resolve"
    ACCEPT_INCOMPLETE = "accept_incomplete"
    EXCLUDE = "exclude"
    MARK_PRIVATE = "mark_private"
    LOCK = "lock"
    UNLOCK = "unlock"
    NEEDS_REVIEW = "needs_review"


class MemoirStoryType(str, Enum):
    EVENT = "event"
    RELATIONSHIP = "relationship"
    LIFE_STAGE = "life_stage"
    THEME = "theme"


class MemoirStoryLifecycleStatus(str, Enum):
    ACTIVE = "active"
    MERGED = "merged"
    ARCHIVED = "archived"


class MemoirStoryCompletionStatus(str, Enum):
    INCOMPLETE = "incomplete"
    SUFFICIENT = "sufficient"
    ACCEPTED_INCOMPLETE = "accepted_incomplete"


class MemoirStoryVerificationStatus(str, Enum):
    UNREVIEWED = "unreviewed"
    CONFIRMED = "confirmed"
    NEEDS_VERIFICATION = "needs_verification"
    DISPUTED = "disputed"
    REJECTED = "rejected"


class MemoirStoryPublicationStatus(str, Enum):
    ELIGIBLE = "eligible"
    PRIVATE = "private"
    EXCLUDED = "excluded"


class MemoirStoryNarrativeRole(str, Enum):
    BACKGROUND = "background"
    TRIGGER = "trigger"
    ACTION = "action"
    TURNING_POINT = "turning_point"
    OUTCOME = "outcome"
    REFLECTION = "reflection"
    CONTEXT = "context"


class MemoirStorySlotStatus(str, Enum):
    MISSING = "missing"
    PARTIAL = "partial"
    SUFFICIENT = "sufficient"
    ACCEPTED_INCOMPLETE = "accepted_incomplete"
    NOT_APPLICABLE = "not_applicable"


class MemoirStoryRevisionChangeType(str, Enum):
    CREATED = "created"
    CLAIMS_UPDATED = "claims_updated"
    SLOTS_RECOMPUTED = "slots_recomputed"
    NEEDS_REVIEW = "needs_review"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    MERGED = "merged"
    SLOT_RESOLVED = "slot_resolved"
    PUBLICATION_CHANGED = "publication_changed"
    BACKFILL_CANDIDATE = "backfill_candidate"


class MemoirChapterContractStatus(str, Enum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"
    SUPERSEDED = "superseded"


class MemoirChapterNodeType(str, Enum):
    OPENING = "opening"
    BACKGROUND = "background"
    STORY = "story"
    TRANSITION = "transition"
    REFLECTION = "reflection"
    CLOSING = "closing"


class MemoirChapterNodeStatus(str, Enum):
    PLANNED = "planned"
    READY = "ready"
    NEEDS_WORK = "needs_work"
    EXCLUDED = "excluded"


class MemoirChapterNodeStoryRole(str, Enum):
    PRIMARY = "primary"
    SUPPORTING = "supporting"
    CONTRAST = "contrast"
    TRANSITION_CONTEXT = "transition_context"


class MemoirDraftBlockRole(str, Enum):
    FACTUAL = "factual"
    TRANSITION = "transition"
    REFLECTION = "reflection"
    OPENING = "opening"
    CLOSING = "closing"
    LEGACY_SNAPSHOT = "legacy_snapshot"


class MemoirDraftBlockEditorialStatus(str, Enum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"
    EXCLUDED = "excluded"


class MemoirDraftBlockFreshnessStatus(str, Enum):
    CURRENT = "current"
    STALE = "stale"


class MemoirDraftBlockClaimUsageRole(str, Enum):
    CORE = "core"
    CONTEXT = "context"
    DETAIL = "detail"
    QUOTED = "quoted"


class MemoirDraftBlockStoryUsageRole(str, Enum):
    PRIMARY = "primary"
    SUPPORTING = "supporting"
    CONTRAST = "contrast"


class MemoirNarrativeGapScopeType(str, Enum):
    STORY = "story"
    CHAPTER = "chapter"
    BOOK = "book"


class MemoirNarrativeGapType(str, Enum):
    MISSING_SLOT = "missing_slot"
    CHRONOLOGY = "chronology"
    RELATIONSHIP = "relationship"
    CAUSALITY = "causality"
    CONTRADICTION = "contradiction"
    REPETITION = "repetition"
    UNCLEAR_REFERENCE = "unclear_reference"
    UNSUPPORTED_FACT = "unsupported_fact"


class MemoirNarrativeGapSeverity(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class MemoirNarrativeGapStatus(str, Enum):
    OPEN = "open"
    RESOLVED = "resolved"
    ACCEPTED_INCOMPLETE = "accepted_incomplete"
    NOT_APPLICABLE = "not_applicable"
    DISMISSED = "dismissed"


class MemoirNarrativeGapDetectedBy(str, Enum):
    RULES = "rules"
    AI = "ai"
    USER = "user"


class MemoirNarrativeGapClaimRole(str, Enum):
    SUPPORTING = "supporting"
    CONFLICTING = "conflicting"
    UNSUPPORTED_CANDIDATE = "unsupported_candidate"


class MemoirNarrativeGapRelationRole(str, Enum):
    PRIMARY = "primary"
    INVOLVED = "involved"


class MemoirFollowupTaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    ANSWERED = "answered"
    DEFERRED = "deferred"
    CANNOT_RECALL = "cannot_recall"
    EXCLUDED = "excluded"
    CANCELLED = "cancelled"


class MemoirFollowupAnswerUse(str, Enum):
    DIRECT_NARRATIVE = "direct_narrative"
    CLARIFICATION = "clarification"
    FACT_RESOLUTION = "fact_resolution"


MEMOIR_STORY_NARRATION_TARGET = "story_narration"


class MemoirFollowupTaskClaimRole(str, Enum):
    KNOWN = "known"
    DO_NOT_REPEAT = "do_not_repeat"
    EXPECTED_EVIDENCE = "expected_evidence"


class MemoirFollowupTaskUtteranceRole(str, Enum):
    TARGET_ANSWER = "target_answer"
    RELATED_CONTEXT = "related_context"
