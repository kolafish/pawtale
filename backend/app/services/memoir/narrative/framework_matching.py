from __future__ import annotations

import hashlib

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.domain.memoir.narrative_types import (
    MemoirClaimEvidenceRole,
    MemoirFrameworkMatchSource,
)
from app.domain.memoir.types import MemoirEditingJobStatus, MemoirEditingJobType
from app.models.memoir import MemoirEditingJob
from app.models.memoir_narrative import MemoirClaim
from app.services.ai_factory import get_ai_service
from app.services.ai_types import AITextClient
from app.services.json_utils import extract_json_payload
from app.services.memoir.editing_jobs import EditingJobResult, enqueue_editing_job
from app.services.memoir.narrative import frameworks
from app.services.memoir.narrative.claim_usage_policy import (
    known_for_conversation_filters,
)


FRAMEWORK_MATCHER_VERSION = "memoir-topic-map-shadow-1"
MAX_MATCH_CLAIMS = 20
MAX_MATCHES_PER_CLAIM = 4


class FrameworkMatchSelectionError(ValueError):
    pass


def _normalized_claim_ids(claim_ids: list[object]) -> list[str]:
    return list(
        dict.fromkeys(
            claim_id.strip()
            for claim_id in claim_ids
            if isinstance(claim_id, str) and claim_id.strip()
        )
    )


def _matching_prompt(
    *,
    project_framework,
    claims: list[MemoirClaim],
) -> str:
    item_lines = "\n".join(
        (
            f"- {item['item_key']} | {item['title']} | "
            f"{item['intent']} | 提示：{'、'.join(item['evidence_hints'])}"
        )
        for item in project_framework.template_snapshot_json["items"]
    )
    claim_lines = "\n".join(
        (
            f"- {claim.id} | 类型={claim.claim_type} | "
            f"内容={claim.statement.strip()[:500]}"
        )
        for claim in claims
    )
    return f"""把回忆录 Claim 被动匹配到项目话题地图。

规则：
- 这是 Shadow 评估，不决定下一题，不创建任务，不判断内容是否足够。
- 只匹配 Claim 的主要语义；不要因为一句话顺带出现某个人或地点就把它匹配到所有相关 item。
- 一个 Claim 可以匹配多个 item，但通常不超过 {MAX_MATCHES_PER_CLAIM} 个。
- 只有明确相关时才输出；没有合适 item 时返回空 matches。
- evidence_role 只能是 supports 或 contradicts。
- confidence 是 0 到 1 的数值。
- 不判断事实真假、内容能否入书、隐私范围或用户决定。
- 只输出 JSON：
{{"matches":[{{"claim_id":"id","item_key":"key","evidence_role":"supports","confidence":0.8}}]}}

话题地图：
{item_lines}

Claims：
{claim_lines}
"""


async def select_framework_matches(
    *,
    project_framework,
    claims: list[MemoirClaim],
    ai_client: AITextClient,
) -> tuple[frameworks.FrameworkClaimMatch, ...]:
    content = await ai_client._call_llm(
        (
            "你是回忆录话题地图匹配器。"
            "你只返回受控 item 关联，不生成问题，不修改任何业务状态。"
        ),
        _matching_prompt(project_framework=project_framework, claims=claims),
        temperature=0.0,
        max_tokens=2500,
        timeout=30.0,
    )
    payload = extract_json_payload(str(content))
    if not isinstance(payload, dict) or not isinstance(payload.get("matches"), list):
        raise FrameworkMatchSelectionError("topic_map_match_invalid_json")
    claim_ids = {claim.id for claim in claims}
    item_keys = {item.item_key for item in project_framework.items}
    accepted: dict[tuple[str, str], frameworks.FrameworkClaimMatch] = {}
    for raw in payload["matches"]:
        if not isinstance(raw, dict):
            raise FrameworkMatchSelectionError("topic_map_match_invalid_shape")
        claim_id = str(raw.get("claim_id") or "").strip()
        item_key = str(raw.get("item_key") or "").strip()
        role_value = str(raw.get("evidence_role") or "").strip()
        try:
            raw_confidence = raw.get("confidence")
            if isinstance(raw_confidence, bool):
                raise ValueError
            confidence = float(raw_confidence)
            role = MemoirClaimEvidenceRole(role_value)
        except (TypeError, ValueError) as exc:
            raise FrameworkMatchSelectionError(
                "topic_map_match_invalid_value"
            ) from exc
        if claim_id not in claim_ids or item_key not in item_keys:
            raise FrameworkMatchSelectionError("topic_map_match_outside_candidates")
        if not 0 <= confidence <= 1:
            raise FrameworkMatchSelectionError("topic_map_match_invalid_confidence")
        match = frameworks.FrameworkClaimMatch(
            claim_id=claim_id,
            item_key=item_key,
            evidence_role=role,
            confidence=confidence,
        )
        key = (claim_id, item_key)
        if key not in accepted or accepted[key].confidence < confidence:
            accepted[key] = match
    limited: list[frameworks.FrameworkClaimMatch] = []
    for claim_id in sorted(claim_ids):
        claim_matches = sorted(
            (
                match
                for (matched_claim_id, _), match in accepted.items()
                if matched_claim_id == claim_id
            ),
            key=lambda match: (-match.confidence, match.item_key),
        )
        if len(claim_matches) > MAX_MATCHES_PER_CLAIM:
            raise FrameworkMatchSelectionError(
                "topic_map_match_too_many_items"
            )
        limited.extend(claim_matches)
    return tuple(limited)


def _matching_dedupe_key(
    *,
    project_id: str,
    claim_ids: list[str],
    match_source: MemoirFrameworkMatchSource,
) -> str:
    fingerprint = hashlib.sha256(
        "\n".join(sorted(claim_ids)).encode("utf-8")
    ).hexdigest()[:20]
    return (
        f"topic_map:{project_id}:{match_source.value}:"
        f"{FRAMEWORK_MATCHER_VERSION}:{fingerprint}"
    )


async def schedule_framework_matching_job(
    db: AsyncSession,
    *,
    project_id: str,
    claim_ids: list[str],
    match_source: MemoirFrameworkMatchSource,
    ai_model: str | None = None,
    enabled: bool | None = None,
    rebuild_run_id: str | None = None,
) -> MemoirEditingJob | None:
    settings = get_settings()
    is_enabled = (
        settings.memoir_topic_map_shadow_enabled
        if enabled is None
        else enabled
    )
    normalized_ids = _normalized_claim_ids(list(claim_ids))
    if (
        not is_enabled
        or not normalized_ids
        or match_source == MemoirFrameworkMatchSource.MANUAL
    ):
        return None
    if len(normalized_ids) > MAX_MATCH_CLAIMS:
        raise ValueError(
            f"topic map matching accepts at most {MAX_MATCH_CLAIMS} claims per job"
        )
    selected_model = settings.memoir_topic_map_shadow_model.strip() or (
        ai_model or ""
    ).strip()
    payload: dict[str, object] = {
        "claim_ids": normalized_ids,
        "match_source": match_source.value,
        "matcher_version": FRAMEWORK_MATCHER_VERSION,
    }
    if selected_model:
        payload["ai_model"] = selected_model
    if rebuild_run_id:
        payload["rebuild_run_id"] = rebuild_run_id
    return await enqueue_editing_job(
        db,
        project_id=project_id,
        job_type=MemoirEditingJobType.EVALUATE_TOPIC_MAP_CLAIMS,
        payload_json=payload,
        dedupe_key=_matching_dedupe_key(
            project_id=project_id,
            claim_ids=normalized_ids,
            match_source=match_source,
        ),
        retry_failed=True,
    )


async def schedule_framework_matching_jobs(
    db: AsyncSession,
    *,
    project_id: str,
    claim_ids: list[str],
    match_source: MemoirFrameworkMatchSource,
    ai_model: str | None = None,
    enabled: bool | None = None,
    rebuild_run_id: str | None = None,
) -> list[MemoirEditingJob]:
    normalized_ids = _normalized_claim_ids(list(claim_ids))
    jobs: list[MemoirEditingJob] = []
    for offset in range(0, len(normalized_ids), MAX_MATCH_CLAIMS):
        job = await schedule_framework_matching_job(
            db,
            project_id=project_id,
            claim_ids=normalized_ids[offset:offset + MAX_MATCH_CLAIMS],
            match_source=match_source,
            ai_model=ai_model,
            enabled=enabled,
            rebuild_run_id=rebuild_run_id,
        )
        if job is not None:
            jobs.append(job)
    return jobs


async def schedule_project_framework_backfill(
    db: AsyncSession,
    *,
    project_id: str,
    enabled: bool | None = None,
) -> int:
    await frameworks.initialize_project_framework(db, project_id=project_id)
    claim_ids = list(
        (
            await db.scalars(
                select(MemoirClaim.id)
                .where(
                    MemoirClaim.project_id == project_id,
                    *known_for_conversation_filters(MemoirClaim),
                )
                .order_by(MemoirClaim.created_at, MemoirClaim.id)
            )
        ).all()
    )
    return len(
        await schedule_framework_matching_jobs(
            db,
            project_id=project_id,
            claim_ids=claim_ids,
            match_source=MemoirFrameworkMatchSource.BACKFILL,
            enabled=enabled,
        )
    )


async def run_framework_matching_job(
    db: AsyncSession,
    job: MemoirEditingJob,
    *,
    ai_client: AITextClient | None = None,
) -> EditingJobResult:
    payload = job.payload_json if isinstance(job.payload_json, dict) else {}
    raw_claim_ids = payload.get("claim_ids")
    if not isinstance(raw_claim_ids, list):
        return EditingJobResult(
            status=MemoirEditingJobStatus.SKIPPED,
            error_message="topic_map_match_source_unavailable",
        )
    claim_ids = _normalized_claim_ids(raw_claim_ids)
    if not claim_ids or len(claim_ids) > MAX_MATCH_CLAIMS:
        return EditingJobResult(
            status=MemoirEditingJobStatus.SKIPPED,
            error_message="topic_map_match_source_unavailable",
        )
    if str(payload.get("matcher_version") or "") != FRAMEWORK_MATCHER_VERSION:
        return EditingJobResult(
            status=MemoirEditingJobStatus.SKIPPED,
            error_message="topic_map_match_version_unavailable",
        )
    try:
        match_source = MemoirFrameworkMatchSource(
            str(payload.get("match_source") or "")
        )
    except ValueError:
        return EditingJobResult(
            status=MemoirEditingJobStatus.SKIPPED,
            error_message="topic_map_match_source_unavailable",
        )
    if match_source == MemoirFrameworkMatchSource.MANUAL:
        return EditingJobResult(
            status=MemoirEditingJobStatus.SKIPPED,
            error_message="topic_map_match_source_unavailable",
        )
    project_framework = await frameworks.initialize_project_framework(
        db,
        project_id=job.project_id,
    )
    claims = list(
        (
            await db.scalars(
                select(MemoirClaim).where(
                    MemoirClaim.project_id == job.project_id,
                    MemoirClaim.id.in_(claim_ids),
                    *known_for_conversation_filters(MemoirClaim),
                )
            )
        ).all()
    )
    if not claims:
        return EditingJobResult(
            status=MemoirEditingJobStatus.SKIPPED,
            error_message="topic_map_match_source_unavailable",
        )
    model = str(payload.get("ai_model") or "").strip() or None
    client = ai_client or get_ai_service(model)
    matches = await select_framework_matches(
        project_framework=project_framework,
        claims=claims,
        ai_client=client,
    )
    result = await frameworks.reconcile_automatic_claim_matches(
        db,
        project_framework=project_framework,
        claims=claims,
        matches=matches,
        match_source=match_source,
        matcher_version=FRAMEWORK_MATCHER_VERSION,
    )
    return EditingJobResult(
        result_json={
            "claim_ids": sorted(claim.id for claim in claims),
            "affected_item_ids": list(result.affected_item_ids),
            "created_relations": result.created,
            "updated_relations": result.updated,
            "removed_relations": result.removed,
            "match_count": len(matches),
            "matches": [
                {
                    "claim_id": match.claim_id,
                    "item_key": match.item_key,
                    "evidence_role": match.evidence_role.value,
                    "confidence": match.confidence,
                }
                for match in matches
            ],
            "match_source": match_source.value,
            "matcher_version": FRAMEWORK_MATCHER_VERSION,
            "provider": client.provider_name,
            "model": client.model_name,
        }
    )
