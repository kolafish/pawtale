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


class MemoirFrameworkEvidenceState(str, Enum):
    NONE = "none"
    HAS_MATERIAL = "has_material"


class MemoirFrameworkUserResolution(str, Enum):
    NONE = "none"
    ENOUGH_FOR_NOW = "enough_for_now"
    NOT_APPLICABLE = "not_applicable"
    DO_NOT_DISCUSS = "do_not_discuss"


class MemoirFrameworkMatchSource(str, Enum):
    MANUAL = "manual"
    EVALUATOR = "evaluator"
    BACKFILL = "backfill"


class MemoirNarrativeDecisionTargetType(str, Enum):
    ENTITY = "entity"
    CLAIM = "claim"
    STORY = "story"
    COMPLETION_ITEM = "completion_item"


class MemoirNarrativeDecisionAction(str, Enum):
    CONFIRM = "confirm"
    INCLUDE = "include"
    REJECT = "reject"
    MERGE = "merge"
    RESOLVE = "resolve"
    EXCLUDE = "exclude"
    MARK_PRIVATE = "mark_private"
    NEEDS_REVIEW = "needs_review"
    UNABLE_TO_ANSWER = "unable_to_answer"
    STOP_ASKING = "stop_asking"


class MemoirStoryType(str, Enum):
    EVENT = "event"
    RELATIONSHIP = "relationship"
    LIFE_STAGE = "life_stage"
    THEME = "theme"


class MemoirStoryLifecycleStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


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


class MemoirCompletionItemStatus(str, Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    DEFERRED = "deferred"
    RESOLVED = "resolved"
    UNABLE_TO_ANSWER = "unable_to_answer"


class MemoirNarrativeGapRecommendedAction(str, Enum):
    INTERVIEW = "interview"
    EDITORIAL = "editorial"
    MANUAL_REVIEW = "manual_review"


class MemoirNarrativeGapActionReasonCode(str, Enum):
    MISSING_NEW_EVIDENCE = "missing_new_evidence"
    EXISTING_MATERIAL_AVAILABLE = "existing_material_available"
    FACT_CONFLICT_DETECTED = "fact_conflict_detected"
    PUBLICATION_DECISION_REQUIRED = "publication_decision_required"
    DRAFT_USAGE_PROBLEM = "draft_usage_problem"


class MemoirNarrativeGapDetectedBy(str, Enum):
    RULES = "rules"
    AI = "ai"
    USER = "user"


class MemoirNarrativeGapClaimRole(str, Enum):
    SUPPORTING = "supporting"
    CONFLICTING = "conflicting"
    UNSUPPORTED_CANDIDATE = "unsupported_candidate"


MEMOIR_STORY_NARRATION_TARGET = "story_narration"


class MemoirInterviewBriefSourceKind(str, Enum):
    OPEN_NARRATION = "open_narration"
    FRAMEWORK = "framework"
    BOOK_COMPLETION = "book_completion"


class MemoirInterviewAction(str, Enum):
    CONTINUE_LISTENING = "continue_listening"
    FOLLOW_CURRENT_STORY = "follow_current_story"
    ASK_PERSON_RELATIONSHIP = "ask_person_relationship"
    ASK_CAUSE_OR_RESULT = "ask_cause_or_result"
    SUMMARIZE_AND_CONFIRM = "summarize_and_confirm"
    RETURN_TO_PRIOR_STORY = "return_to_prior_story"
    OFFER_NEW_TOPIC = "offer_new_topic"
    PAUSE_OR_END = "pause_or_end"


class MemoirInterviewActionRisk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    TERMINAL = "terminal"


class MemoirInterviewStoryState(str, Enum):
    NONE = "none"
    OPENING = "opening"
    ACTIVE = "active"
    NEEDS_CONFIRMATION = "needs_confirmation"
    CLOSING = "closing"


class MemoirConversationMomentum(str, Enum):
    LOW = "low"
    STEADY = "steady"
    HIGH = "high"


class MemoirInterviewPolicyExecutionMode(str, Enum):
    SHADOW = "shadow"
    SUGGEST = "suggest"
    ACTIVE = "active"


class MemoirInterviewResponseOutcome(str, Enum):
    CONTINUED_STORY = "continued_story"
    ANSWERED_QUESTION = "answered_question"
    CHANGED_TOPIC = "changed_topic"
    CONFUSED = "confused"
    STOPPED = "stopped"


class MemoirInterviewPolicyFailureCode(str, Enum):
    ACTION_OUTSIDE_CANDIDATES = "action_outside_candidates"
    INTERRUPTED_USER = "interrupted_user"
    REPEATED_QUESTION = "repeated_question"
    WRONG_TOPIC_SWITCH = "wrong_topic_switch"
    PRIVACY_VIOLATION = "privacy_violation"
