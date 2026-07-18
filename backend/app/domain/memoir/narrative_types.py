from enum import Enum


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
