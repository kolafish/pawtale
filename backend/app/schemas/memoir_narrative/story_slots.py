from pydantic import BaseModel, ConfigDict

from app.domain.memoir.narrative_types import MemoirStoryType


CURRENT_STORY_SLOT_SCHEMA_VERSION = "1"


class MemoirStorySlotDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    required: bool


class MemoirStorySlotSchema(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    story_type: MemoirStoryType
    version: str
    slots: tuple[MemoirStorySlotDefinition, ...]

    @property
    def slot_keys(self) -> tuple[str, ...]:
        return tuple(slot.key for slot in self.slots)

    @property
    def required_slot_keys(self) -> tuple[str, ...]:
        return tuple(slot.key for slot in self.slots if slot.required)


def _schema(
    story_type: MemoirStoryType,
    *,
    required: tuple[str, ...],
    optional: tuple[str, ...],
) -> MemoirStorySlotSchema:
    return MemoirStorySlotSchema(
        story_type=story_type,
        version=CURRENT_STORY_SLOT_SCHEMA_VERSION,
        slots=tuple(
            [MemoirStorySlotDefinition(key=key, required=True) for key in required]
            + [MemoirStorySlotDefinition(key=key, required=False) for key in optional]
        ),
    )


STORY_SLOT_SCHEMAS: dict[tuple[MemoirStoryType, str], MemoirStorySlotSchema] = {
    (MemoirStoryType.EVENT, "1"): _schema(
        MemoirStoryType.EVENT,
        required=("background", "trigger", "people", "actions", "outcome"),
        optional=("difficulty", "turning_point", "reflection"),
    ),
    (MemoirStoryType.RELATIONSHIP, "1"): _schema(
        MemoirStoryType.RELATIONSHIP,
        required=("person_portrait", "shared_experience", "representative_scene"),
        optional=("meeting", "relationship_change", "reflection"),
    ),
    (MemoirStoryType.LIFE_STAGE, "1"): _schema(
        MemoirStoryType.LIFE_STAGE,
        required=("entry_reason", "responsibilities", "representative_events", "transition"),
        optional=("ability_and_character", "reflection"),
    ),
    (MemoirStoryType.THEME, "1"): _schema(
        MemoirStoryType.THEME,
        required=("theme_definition", "supporting_cases", "continuity"),
        optional=("contrast_or_failure", "reflection"),
    ),
}


def story_slot_schema(
    story_type: MemoirStoryType | str,
    version: str = CURRENT_STORY_SLOT_SCHEMA_VERSION,
) -> MemoirStorySlotSchema:
    normalized_type = MemoirStoryType(story_type)
    try:
        return STORY_SLOT_SCHEMAS[(normalized_type, version)]
    except KeyError as exc:
        raise ValueError(
            f"unsupported story slot schema: {normalized_type.value}@{version}"
        ) from exc
