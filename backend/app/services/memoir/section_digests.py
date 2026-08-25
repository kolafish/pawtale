from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import logging
import re
import time
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.types import MemoirSemanticCacheStatus
from app.models.memoir import (
    MemoirSection,
    MemoirSectionDigest,
    MemoirSectionMaterial,
)
from app.schemas.memoir_section_digests import MemoirSectionDigestResponse
from app.services.ai_calls import (
    AIInvalidStructuredOutputError,
    AIOutputTruncatedError,
    generate_structured_with_client,
)
from app.services.ai_factory import get_ai_service
from app.services.ai_references import ModelReferenceMap
from app.services.ai_timeouts import (
    AITextTimeoutProfile,
    ai_error_metadata,
    ai_text_timeout_seconds,
    ai_user_error_message,
)
from app.services.ai_types import AITextClient
from app.services.ai_workloads import (
    AITextWorkload,
    ai_text_workload_policy,
)
from app.services.memoir.semantic_state import (
    semantic_json_signature,
    semantic_text_hash,
)
from app.services.memoir.transcript_projection import current_material_text


SECTION_DIGEST_ENGINE = "llm_section_digest_v2"
SECTION_DIGEST_SYSTEM_PROMPT = (
    "你是一位克制、敏锐的中文回忆录编辑。"
    "你只根据给定小章节和原始讲述判断读者是否容易理解，不补写事实。"
)
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SectionDigestMaterial:
    material_id: str
    section_material_id: str
    transcript_version_id: str | None
    sort_order: int
    text: str
    include_in_book: bool


@dataclass(frozen=True)
class SectionDigestContext:
    project_id: str
    section_id: str
    title: str
    section_type: str
    section_version: int
    include_in_book: bool
    materials: tuple[SectionDigestMaterial, ...]
    input_signature: str


@dataclass(frozen=True)
class SectionDigestGeneration:
    digest_json: dict
    timeout_seconds: float
    response_diagnostics: dict[str, object]


class _SectionDigestIssueOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issue_type: str = ""
    question: str = ""
    reason: str = ""
    priority: str = ""
    material_ids: list[str] = Field(default_factory=list)


class _SectionDigestOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str
    focus: str = ""
    covered_points: list[str] = Field(default_factory=list)
    editorial_guidance: list[str] = Field(default_factory=list)
    candidate_issues: list[_SectionDigestIssueOutput] = Field(default_factory=list)


def _compact_text(value: object) -> str:
    return re.sub(r"\s+", " ", value if isinstance(value, str) else "").strip()


def _trim_text(value: object, *, limit: int) -> str:
    return _compact_text(value)[:limit]


def _string_list(
    value: object,
    *,
    limit: int,
    item_limit: int,
) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = _trim_text(item, limit=item_limit)
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def section_digest_context(section: MemoirSection) -> SectionDigestContext:
    ordered = sorted(
        section.materials,
        key=lambda item: (item.sort_order, item.id),
    )
    materials = tuple(
        SectionDigestMaterial(
            material_id=material.utterance_id,
            section_material_id=material.id,
            transcript_version_id=material.transcript_version_id,
            sort_order=material.sort_order,
            text=_compact_text(current_material_text(material)),
            include_in_book=material.include_in_book,
        )
        for material in ordered
    )
    signature_payload = {
        "section": {
            "id": section.id,
            "title": section.title,
            "section_type": section.section_type,
            "version": section.version,
            "status": section.status,
            "include_in_book": section.include_in_book,
        },
        "materials": [
            {
                "section_material_id": material.section_material_id,
                "material_id": material.material_id,
                "transcript_version_id": material.transcript_version_id,
                "sort_order": material.sort_order,
                "include_in_book": material.include_in_book,
                "text_hash": (
                    semantic_text_hash(material.text)
                    if material.include_in_book
                    else ""
                ),
            }
            for material in materials
        ],
        "engine": SECTION_DIGEST_ENGINE,
    }
    return SectionDigestContext(
        project_id=section.project_id,
        section_id=section.id,
        title=section.title,
        section_type=section.section_type,
        section_version=section.version,
        include_in_book=section.include_in_book,
        materials=materials,
        input_signature=semantic_json_signature(signature_payload),
    )


async def load_section_digest_context(
    db: AsyncSession,
    *,
    project_id: str,
    section_id: str,
) -> SectionDigestContext:
    section = await db.scalar(
        select(MemoirSection)
        .where(
            MemoirSection.project_id == project_id,
            MemoirSection.id == section_id,
        )
        .options(
            selectinload(MemoirSection.materials).selectinload(
                MemoirSectionMaterial.utterance
            ),
            selectinload(MemoirSection.materials).selectinload(
                MemoirSectionMaterial.transcript_version
            ),
        )
    )
    if section is None:
        raise HTTPException(status_code=404, detail="小章节不存在")
    return section_digest_context(section)


_TYPE_GUIDANCE = {
    "event": "关注读者能否理解事情的背景、发展、转折和影响，不要求补齐所有时间地点。",
    "life_fragment": "关注这个生活片段是否有清楚的场景和个人感受，不强求完整事件结构。",
    "person_portrait": "关注这个人和讲述者的关系、鲜明特点以及为何重要。",
    "life_stage_summary": "关注这一阶段的主要变化、代表经历和讲述者如何看待它。",
    "viewpoint_reflection": "关注观点从何而来、表达是否清楚，以及它和个人经历的联系。",
}


def _material_references(
    context: SectionDigestContext,
) -> ModelReferenceMap:
    return ModelReferenceMap(
        "M",
        (
            material.material_id
            for material in context.materials
            if material.include_in_book
        ),
    )


def build_section_digest_prompt(context: SectionDigestContext) -> str:
    references = _material_references(context)
    materials = "\n\n".join(
        (
            f"material_id: {references.encode(material.material_id)}\n"
            f"原始讲述: {material.text}"
        )
        for material in context.materials
        if material.include_in_book
    ) or "（暂无讲述素材）"
    guidance = _TYPE_GUIDANCE.get(
        context.section_type,
        "根据这段内容本身的叙事意图判断，不套固定模板。",
    )
    return f"""请审阅下面这个回忆录小章节。

标题：{context.title}
类型：{context.section_type}
审阅侧重：{guidance}

有序讲述素材：
{materials}

请输出严格 JSON，不要 Markdown，不要解释：
{{
  "summary": "一到三句话概括已经讲述的内容",
  "focus": "这个小章节真正想表达的重点",
  "covered_points": ["已经讲清楚的要点"],
  "editorial_guidance": ["只用现有素材即可处理的去重、顺序、衔接、标题或表达建议"],
  "candidate_issues": [
    {{
      "issue_type": "context|sequence|relationship|motivation|impact|meaning|clarity",
      "question": "如果确实有必要补充，给用户的一句自然问题",
      "reason": "为什么这个问题会明显帮助读者理解",
      "priority": "high|medium|low",
      "material_ids": ["与判断直接相关的 material_id"]
    }}
  ]
}}

要求：
- 先理解这一段真正想表达什么，不要逐条检查时间、地点、人物、起因和结果。
- 能通过编辑现有素材解决的问题写入 editorial_guidance，不要向用户提问。
- 只提出会明显改善读者理解的问题；内容已经自然成立时 candidate_issues 可以为空。
- candidate_issues 最多三个，并按价值排序。
- 不要要求讲述者证明事实，不要创造素材中没有的信息。
- material_ids 只能使用输入中出现的本次请求短编号 material_id。
- 输入中只包含当前允许收录的素材。
"""


def normalize_section_digest_payload(
    payload: Any,
    *,
    material_references: ModelReferenceMap,
) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("section digest AI did not return a JSON object")
    summary = _trim_text(payload.get("summary"), limit=600)
    focus = _trim_text(payload.get("focus"), limit=300)
    if not summary:
        raise ValueError("section digest summary is empty")
    issues_value = payload.get("candidate_issues")
    if not isinstance(issues_value, list):
        issues_value = []
    issues: list[dict[str, object]] = []
    allowed_types = {
        "context",
        "sequence",
        "relationship",
        "motivation",
        "impact",
        "meaning",
        "clarity",
    }
    allowed_priorities = {"high", "medium", "low"}
    for item in issues_value:
        if not isinstance(item, dict):
            continue
        question = _trim_text(item.get("question"), limit=180)
        reason = _trim_text(item.get("reason"), limit=300)
        if not question or not reason:
            continue
        material_ids = material_references.decode_many(
            item.get("material_ids"),
            limit=8,
        )
        issue_type = _trim_text(item.get("issue_type"), limit=30)
        priority = _trim_text(item.get("priority"), limit=20)
        issues.append(
            {
                "issue_type": issue_type if issue_type in allowed_types else "clarity",
                "question": question,
                "reason": reason,
                "priority": priority if priority in allowed_priorities else "medium",
                "material_ids": material_ids,
            }
        )
        if len(issues) >= 3:
            break
    return {
        "summary": summary,
        "focus": focus,
        "covered_points": _string_list(
            payload.get("covered_points"),
            limit=8,
            item_limit=120,
        ),
        "editorial_guidance": _string_list(
            payload.get("editorial_guidance"),
            limit=6,
            item_limit=240,
        ),
        "candidate_issues": issues,
    }


async def generate_section_digest_payload(
    context: SectionDigestContext,
    *,
    ai_model: str | None = None,
    ai_client: AITextClient | None = None,
) -> SectionDigestGeneration:
    prompt = build_section_digest_prompt(context)
    client = ai_client or get_ai_service(ai_model)
    policy = ai_text_workload_policy(AITextWorkload.GENERATION_STRUCTURED)
    timeout_seconds = ai_text_timeout_seconds(policy.timeout_profile)
    generated = await generate_structured_with_client(
        client,
        SECTION_DIGEST_SYSTEM_PROMPT,
        prompt,
        output_model=_SectionDigestOutput,
        requested_model=ai_model,
        temperature=0.2,
        max_tokens=policy.max_tokens,
        timeout=timeout_seconds,
        max_attempts=policy.max_attempts,
    )
    return SectionDigestGeneration(
        digest_json=normalize_section_digest_payload(
            generated.value.model_dump(),
            material_references=_material_references(context),
        ),
        timeout_seconds=timeout_seconds,
        response_diagnostics=generated.response.diagnostics(),
    )


async def _mark_previous_digests_stale(
    db: AsyncSession,
    *,
    project_id: str,
    section_id: str,
    engine: str,
    current_signature: str,
) -> None:
    digests = list(
        (
            await db.execute(
                select(MemoirSectionDigest).where(
                    MemoirSectionDigest.project_id == project_id,
                    MemoirSectionDigest.section_id == section_id,
                    MemoirSectionDigest.engine == engine,
                    MemoirSectionDigest.input_signature != current_signature,
                    MemoirSectionDigest.status
                    == MemoirSemanticCacheStatus.READY.value,
                )
            )
        ).scalars().all()
    )
    for digest in digests:
        digest.status = MemoirSemanticCacheStatus.STALE.value
        digest.updated_at = datetime.now(UTC).replace(tzinfo=None)
        db.add(digest)
    if digests:
        await db.flush()


async def mark_section_digests_stale(
    db: AsyncSession,
    *,
    project_id: str,
    section_ids: set[str],
) -> int:
    if not section_ids:
        return 0
    digests = list(
        (
            await db.execute(
                select(MemoirSectionDigest).where(
                    MemoirSectionDigest.project_id == project_id,
                    MemoirSectionDigest.section_id.in_(section_ids),
                    MemoirSectionDigest.status
                    == MemoirSemanticCacheStatus.READY.value,
                )
            )
        ).scalars().all()
    )
    now = datetime.now(UTC).replace(tzinfo=None)
    for digest in digests:
        digest.status = MemoirSemanticCacheStatus.STALE.value
        digest.updated_at = now
        db.add(digest)
    if digests:
        await db.flush()
    return len(digests)


async def _upsert_digest(
    db: AsyncSession,
    *,
    context: SectionDigestContext,
    engine: str,
    status: MemoirSemanticCacheStatus,
    digest_json: dict,
    error_message: str = "",
) -> MemoirSectionDigest:
    digest = await db.scalar(
        select(MemoirSectionDigest).where(
            MemoirSectionDigest.project_id == context.project_id,
            MemoirSectionDigest.section_id == context.section_id,
            MemoirSectionDigest.engine == engine,
            MemoirSectionDigest.input_signature == context.input_signature,
        )
    )
    if digest is None:
        digest = MemoirSectionDigest(
            project_id=context.project_id,
            section_id=context.section_id,
            input_signature=context.input_signature,
            engine=engine,
        )
    digest.status = status.value
    digest.digest_json = digest_json
    digest.error_message = error_message
    digest.updated_at = datetime.now(UTC).replace(tzinfo=None)
    db.add(digest)
    await db.flush()
    return digest


async def generate_or_reuse_section_digest(
    db: AsyncSession,
    *,
    project_id: str,
    section_id: str,
    ai_model: str | None = None,
    ai_client: AITextClient | None = None,
    engine: str = SECTION_DIGEST_ENGINE,
    force_regenerate: bool = False,
) -> MemoirSectionDigest:
    context = await load_section_digest_context(
        db,
        project_id=project_id,
        section_id=section_id,
    )
    await _mark_previous_digests_stale(
        db,
        project_id=project_id,
        section_id=section_id,
        engine=engine,
        current_signature=context.input_signature,
    )
    # Persist stale-cache bookkeeping before the long provider call.
    await db.commit()
    existing = await db.scalar(
        select(MemoirSectionDigest).where(
            MemoirSectionDigest.project_id == project_id,
            MemoirSectionDigest.section_id == section_id,
            MemoirSectionDigest.engine == engine,
            MemoirSectionDigest.input_signature == context.input_signature,
            MemoirSectionDigest.status == MemoirSemanticCacheStatus.READY.value,
        )
    )
    if existing is not None and not force_regenerate:
        return existing
    included_materials = [
        material
        for material in context.materials
        if material.include_in_book and material.text
    ]
    if not included_materials:
        return await _upsert_digest(
            db,
            context=context,
            engine=engine,
            status=MemoirSemanticCacheStatus.FAILED,
            digest_json={},
            error_message="section has no included material",
        )
    client = ai_client or get_ai_service(ai_model)
    timeout_seconds = ai_text_timeout_seconds(AITextTimeoutProfile.GENERATION)
    started_at = time.monotonic()
    try:
        generated = await generate_section_digest_payload(
            context,
            ai_model=ai_model,
            ai_client=client,
        )
    except Exception as exc:
        logger.exception(
            "memoir section digest generation failed section_id=%s",
            section_id,
        )
        if isinstance(exc, AIOutputTruncatedError):
            error_message = "小节审阅输出被截断，请重试"
        elif isinstance(exc, AIInvalidStructuredOutputError):
            error_message = "小节审阅没有返回可识别的结构，请重试"
        else:
            error_message = ai_user_error_message(
                exc,
                "小节审阅服务暂时不可用，请稍后重试",
            )
        return await _upsert_digest(
            db,
            context=context,
            engine=engine,
            status=MemoirSemanticCacheStatus.FAILED,
            digest_json={
                "generation": {
                    **ai_error_metadata(
                        exc,
                        requested_model=ai_model,
                        provider=getattr(client, "provider_name", None),
                        resolved_model=getattr(client, "model_name", ai_model),
                        timeout_seconds=timeout_seconds,
                        elapsed_ms=round((time.monotonic() - started_at) * 1000),
                    ),
                    "engine": engine,
                }
            },
            error_message=error_message,
        )
    return await _upsert_digest(
        db,
        context=context,
        engine=engine,
        status=MemoirSemanticCacheStatus.READY,
        digest_json={
            **generated.digest_json,
            "generation": {
                **generated.response_diagnostics,
                "engine": engine,
                "timeout_seconds": generated.timeout_seconds,
            },
        },
    )


def digest_response(digest: MemoirSectionDigest) -> MemoirSectionDigestResponse:
    return MemoirSectionDigestResponse(
        id=digest.id,
        project_id=digest.project_id,
        section_id=digest.section_id,
        input_signature=digest.input_signature,
        engine=digest.engine,
        status=digest.status,
        digest=digest.digest_json,
        error_message=digest.error_message,
        created_at=digest.created_at,
        updated_at=digest.updated_at,
    )


async def get_current_section_digest(
    db: AsyncSession,
    *,
    project_id: str,
    section_id: str,
    engine: str = SECTION_DIGEST_ENGINE,
) -> MemoirSectionDigest | None:
    context = await load_section_digest_context(
        db,
        project_id=project_id,
        section_id=section_id,
    )
    return await db.scalar(
        select(MemoirSectionDigest).where(
            MemoirSectionDigest.project_id == project_id,
            MemoirSectionDigest.section_id == section_id,
            MemoirSectionDigest.engine == engine,
            MemoirSectionDigest.input_signature == context.input_signature,
            MemoirSectionDigest.status == MemoirSemanticCacheStatus.READY.value,
        )
    )
