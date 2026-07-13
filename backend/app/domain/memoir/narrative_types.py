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
    REJECT = "reject"
    MERGE = "merge"
    RESOLVE = "resolve"
    ACCEPT_INCOMPLETE = "accept_incomplete"
    EXCLUDE = "exclude"
    MARK_PRIVATE = "mark_private"
    LOCK = "lock"
    UNLOCK = "unlock"
    NEEDS_REVIEW = "needs_review"
