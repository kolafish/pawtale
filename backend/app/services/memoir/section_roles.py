from __future__ import annotations


SECTION_SYSTEM_ROLE_KEY = "system_role"
PENDING_ASSIGNMENT_SECTION_ROLE = "pending_material_assignment"
PENDING_ASSIGNMENT_SECTION_TITLE = "待归类素材"


def requires_user_assignment(metadata_json: object) -> bool:
    return (
        isinstance(metadata_json, dict)
        and metadata_json.get(SECTION_SYSTEM_ROLE_KEY)
        == PENDING_ASSIGNMENT_SECTION_ROLE
    )
