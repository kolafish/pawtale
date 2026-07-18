from __future__ import annotations

import hashlib

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.narrative_types import MemoirFrameworkMatchSource
from app.domain.memoir.types import MemoirEditingJobStatus, MemoirEditingJobType
from app.models.memoir import (
    MemoirEditingJob,
    MemoirVoiceSession,
    MemoirVoiceUtterance,
)
from app.services.ai_factory import get_ai_service
from app.services.ai_types import AITextClient
from app.services.json_utils import extract_json_payload
from app.services.memoir.editing_jobs import EditingJobResult, enqueue_editing_job
from app.services.memoir.narrative import frameworks
from app.services.memoir.utterance_editing_pipeline import current_utterance_text


FRAMEWORK_MATCHER_VERSION = "memoir-topic-map-material-v1"
MAX_MATCH_MATERIALS = 20
MAX_MATCHES_PER_MATERIAL = 4


class FrameworkMatchSelectionError(ValueError):
    pass


def _normalized_material_ids(material_ids: list[object]) -> list[str]:
    return list(
        dict.fromkeys(
            material_id.strip()
            for material_id in material_ids
            if isinstance(material_id, str) and material_id.strip()
        )
    )


def _matching_prompt(*, project_framework, materials) -> str:
    item_lines = "\n".join(
        (
            f"- {item['item_key']} | {item['title']} | "
            f"{item['intent']} | 提示：{'、'.join(item['evidence_hints'])}"
        )
        for item in project_framework.template_snapshot_json["items"]
    )
    material_lines = "\n".join(
        f"- {material.id} | {current_utterance_text(material)[:800]}"
        for material in materials
    )
    return f"""把每段回忆录讲述整体匹配到话题地图。

规则：
- 一段讲述是一个完整素材，不要拆成 Claim 或逐句匹配。
- 只匹配讲述的主要语义；顺带提到的人、地点或事情不算一个独立话题。
- 每段素材通常只对应一两个话题，明确相关时才输出，无法判断时返回空。
- 不判断事实真假、内容能否入书、隐私范围或用户决定。
- confidence 是 0 到 1 的数值。
- 只输出 JSON：
{{"matches":[{{"utterance_id":"id","item_key":"key","confidence":0.8}}]}}

话题地图：
{item_lines}

讲述素材：
{material_lines}
"""


async def select_framework_matches(
    *,
    project_framework,
    materials: list[MemoirVoiceUtterance],
    ai_client: AITextClient,
) -> tuple[frameworks.FrameworkMaterialMatch, ...]:
    content = await ai_client._call_llm(
        "你是回忆录话题地图匹配器，只返回素材与候选话题的受控关联。",
        _matching_prompt(
            project_framework=project_framework,
            materials=materials,
        ),
        temperature=0.0,
        max_tokens=2500,
        timeout=30.0,
    )
    payload = extract_json_payload(str(content))
    if not isinstance(payload, dict) or not isinstance(
        payload.get("matches"), list
    ):
        raise FrameworkMatchSelectionError("topic_map_match_invalid_json")
    material_ids = {material.id for material in materials}
    item_keys = {item.item_key for item in project_framework.items}
    accepted: dict[tuple[str, str], frameworks.FrameworkMaterialMatch] = {}
    for raw in payload["matches"]:
        if not isinstance(raw, dict):
            raise FrameworkMatchSelectionError("topic_map_match_invalid_shape")
        material_id = str(raw.get("utterance_id") or "").strip()
        item_key = str(raw.get("item_key") or "").strip()
        try:
            raw_confidence = raw.get("confidence")
            if isinstance(raw_confidence, bool):
                raise ValueError
            confidence = float(raw_confidence)
        except (TypeError, ValueError) as exc:
            raise FrameworkMatchSelectionError(
                "topic_map_match_invalid_value"
            ) from exc
        if material_id not in material_ids or item_key not in item_keys:
            raise FrameworkMatchSelectionError(
                "topic_map_match_outside_candidates"
            )
        if not 0 <= confidence <= 1:
            raise FrameworkMatchSelectionError(
                "topic_map_match_invalid_confidence"
            )
        match = frameworks.FrameworkMaterialMatch(
            utterance_id=material_id,
            item_key=item_key,
            confidence=confidence,
        )
        key = (material_id, item_key)
        if key not in accepted or accepted[key].confidence < confidence:
            accepted[key] = match
    limited: list[frameworks.FrameworkMaterialMatch] = []
    for material_id in sorted(material_ids):
        material_matches = sorted(
            (
                match
                for (matched_id, _), match in accepted.items()
                if matched_id == material_id
            ),
            key=lambda match: (-match.confidence, match.item_key),
        )
        if len(material_matches) > MAX_MATCHES_PER_MATERIAL:
            raise FrameworkMatchSelectionError(
                "topic_map_match_too_many_items"
            )
        limited.extend(material_matches)
    return tuple(limited)


async def schedule_framework_matching_job(
    db: AsyncSession,
    *,
    project_id: str,
    material_ids: list[str],
    ai_model: str | None = None,
) -> MemoirEditingJob | None:
    normalized_ids = _normalized_material_ids(list(material_ids))
    if not normalized_ids:
        return None
    if len(normalized_ids) > MAX_MATCH_MATERIALS:
        raise ValueError(
            f"topic map matching accepts at most {MAX_MATCH_MATERIALS} materials"
        )
    fingerprint = hashlib.sha256(
        "\n".join(sorted(normalized_ids)).encode("utf-8")
    ).hexdigest()[:20]
    payload: dict[str, object] = {
        "material_ids": normalized_ids,
        "matcher_version": FRAMEWORK_MATCHER_VERSION,
    }
    if ai_model:
        payload["ai_model"] = ai_model
    return await enqueue_editing_job(
        db,
        project_id=project_id,
        job_type=MemoirEditingJobType.UPDATE_TOPIC_MAP,
        payload_json=payload,
        dedupe_key=(
            f"topic_map_material:{project_id}:"
            f"{FRAMEWORK_MATCHER_VERSION}:{fingerprint}"
        ),
        replace_completed=True,
    )


async def run_framework_matching_job(
    db: AsyncSession,
    job: MemoirEditingJob,
    *,
    ai_client: AITextClient | None = None,
) -> EditingJobResult:
    payload = job.payload_json or {}
    raw_ids = payload.get("material_ids")
    material_ids = (
        _normalized_material_ids(raw_ids)
        if isinstance(raw_ids, list)
        else []
    )
    if not material_ids or len(material_ids) > MAX_MATCH_MATERIALS:
        return EditingJobResult(
            status=MemoirEditingJobStatus.SKIPPED,
            error_message="topic_map_materials_unavailable",
        )
    project_framework = await frameworks.initialize_project_framework(
        db,
        project_id=job.project_id,
    )
    materials = list(
        (
            await db.scalars(
                select(MemoirVoiceUtterance)
                .join(
                    MemoirVoiceSession,
                    MemoirVoiceUtterance.voice_session_id
                    == MemoirVoiceSession.id,
                )
                .where(
                    MemoirVoiceSession.project_id == job.project_id,
                    MemoirVoiceUtterance.id.in_(material_ids),
                )
            )
        ).all()
    )
    materials = [
        material for material in materials if current_utterance_text(material)
    ]
    if not materials:
        return EditingJobResult(
            status=MemoirEditingJobStatus.SKIPPED,
            error_message="topic_map_materials_unavailable",
        )
    model = (
        payload["ai_model"]
        if isinstance(payload.get("ai_model"), str)
        else None
    )
    client = ai_client or get_ai_service(model)
    matches = await select_framework_matches(
        project_framework=project_framework,
        materials=materials,
        ai_client=client,
    )
    result = await frameworks.reconcile_automatic_material_matches(
        db,
        project_framework=project_framework,
        materials=materials,
        matches=matches,
        match_source=MemoirFrameworkMatchSource.EVALUATOR,
        matcher_version=FRAMEWORK_MATCHER_VERSION,
    )
    return EditingJobResult(
        result_json={
            "material_ids": sorted(material.id for material in materials),
            "matches": [
                {
                    "utterance_id": match.utterance_id,
                    "item_key": match.item_key,
                    "confidence": match.confidence,
                }
                for match in matches
            ],
            "created": result.created,
            "updated": result.updated,
            "removed": result.removed,
        }
    )
