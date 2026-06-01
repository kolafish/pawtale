from enum import Enum


class ReferenceType(str, Enum):
    CHARACTER = "character"
    LOCATION = "location"
    NOTE = "note"
    OUTLINE = "outline"


class SettingReferenceType(str, Enum):
    LOCATION = ReferenceType.LOCATION.value
    NOTE = ReferenceType.NOTE.value


def coerce_setting_reference_type(
    value: str | ReferenceType | SettingReferenceType | None,
) -> SettingReferenceType:
    try:
        return SettingReferenceType(value)
    except (TypeError, ValueError):
        return SettingReferenceType.NOTE
