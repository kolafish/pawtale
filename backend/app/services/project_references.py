from sqlalchemy import or_

from app.models.models import Character, Scene
from app.models.projects import ReferenceItem


PROJECT_CHARACTER_METADATA_KEYS = {
    "story_name",
    "role_in_story",
    "relationships",
    "character_arc",
}
def global_character_filter(user_id: str):
    return or_(Character.user_id == user_id, Character.is_preset == True)


def global_scene_filter(user_id: str):
    return or_(Scene.user_id == user_id, Scene.is_preset == True)


def character_metadata(character: Character) -> dict:
    return {
        "global_character_id": character.id,
        "gender": character.gender,
        "age": character.age,
        "identity": character.identity,
        "appearance": character.appearance,
        "personality": character.personality or [],
        "speaking_style": character.speaking_style,
        "core_trait": character.core_trait,
        "life_goal": character.life_goal,
        "fatal_flaw": character.fatal_flaw,
        "growth_hint": character.growth_hint,
        "habits": character.habits or [],
        "secret": character.secret,
    }


def scene_metadata(scene: Scene) -> dict:
    return {
        "global_scene_id": scene.id,
        "category": scene.category,
        "location": scene.location,
        "time_weather": scene.time_weather,
        "mood_keywords": scene.mood_keywords or [],
        "dramatic_potential": scene.dramatic_potential,
        "sensory_details": scene.sensory_details or {},
        "props": scene.props or [],
    }


def character_reference(project_id: str, character: Character) -> ReferenceItem:
    return ReferenceItem(
        project_id=project_id,
        type="character",
        name=character.name,
        content=character.background or "",
        metadata_json=character_metadata(character),
    )


def scene_reference(project_id: str, scene: Scene) -> ReferenceItem:
    return ReferenceItem(
        project_id=project_id,
        type="location",
        name=scene.name,
        content=scene.description or "",
        metadata_json=scene_metadata(scene),
    )


def find_matching_reference(
    refs: list[ReferenceItem],
    ref_type: str,
    global_id: str,
    name: str,
) -> dict | None:
    global_key = "global_character_id" if ref_type == "character" else "global_scene_id"
    for ref in refs:
        if ref.type == ref_type and (ref.metadata_json or {}).get(global_key) == global_id:
            return {"ref_id": ref.id, "ref_name": ref.name, "match_by": "global_id"}
    for ref in refs:
        if ref.type == ref_type and ref.name.strip().lower() == name.strip().lower():
            return {"ref_id": ref.id, "ref_name": ref.name, "match_by": "name"}
    return None


def project_character_metadata(meta: dict) -> dict:
    return {key: meta[key] for key in PROJECT_CHARACTER_METADATA_KEYS if key in meta}


def character_data_from_reference(ref: ReferenceItem, meta: dict) -> dict:
    return {
        "name": ref.name,
        "background": ref.content or "",
        "gender": meta.get("gender"),
        "age": meta.get("age"),
        "identity": meta.get("identity"),
        "appearance": meta.get("appearance"),
        "personality": meta.get("personality") or [],
        "speaking_style": meta.get("speaking_style"),
        "core_trait": meta.get("core_trait"),
        "life_goal": meta.get("life_goal"),
        "fatal_flaw": meta.get("fatal_flaw"),
        "growth_hint": meta.get("growth_hint"),
        "habits": meta.get("habits") or [],
        "secret": meta.get("secret"),
    }


def scene_data_from_reference(ref: ReferenceItem, meta: dict) -> dict:
    return {
        "name": ref.name,
        "description": ref.content or "",
        "category": meta.get("category") or "自定义",
        "location": meta.get("location"),
        "time_weather": meta.get("time_weather"),
        "mood_keywords": meta.get("mood_keywords") or [],
        "dramatic_potential": meta.get("dramatic_potential"),
        "sensory_details": meta.get("sensory_details") or {},
        "props": meta.get("props") or [],
        "is_preset": False,
    }
