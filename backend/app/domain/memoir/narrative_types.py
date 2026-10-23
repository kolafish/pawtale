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
