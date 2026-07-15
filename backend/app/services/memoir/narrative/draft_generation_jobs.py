from __future__ import annotations

from hashlib import sha256
import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.narrative_types import (
    MemoirChapterNodeType,
    MemoirDraftBlockFreshnessStatus,
    MemoirNarrativeActorType,
)
from app.domain.memoir.types import MemoirEditingJobStatus, MemoirEditingJobType
from app.models.memoir import MemoirEditingJob
from app.models.memoir_narrative import MemoirDraftBlock
from app.schemas.memoir_narrative import (
    MemoirDraftBlockGenerationRequest,
    MemoirDraftBlockJobResponse,
    MemoirDraftBlockRevisionApplyRequest,
    MemoirDraftBlockRevisionJobRequest,
)
from app.services.ai_factory import get_ai_service
from app.services.ai_types import AITextClient
from app.services.memoir.editing_jobs import EditingJobResult, enqueue_editing_job
from app.services.memoir.narrative import draft_block_queries, draft_blocks
from app.services.memoir.narrative.draft_block_ai import (
    DraftBlockEditorialContextInput,
    DraftBlockLLMOutput,
    generate_draft_block_text,
    validate_generation_output,
)
from app.services.memoir.narrative.draft_generation_context import (
    build_node_generation_context,
    load_current_contract_nodes,
    select_generation_nodes,
)
from app.services.memoir.narrative.draft_editorial_contexts import (
    editorial_context_signature,
    load_chapter_editorial_contexts,
)


JOB_MODE_GENERATE = "generate"
JOB_MODE_REVISE = "revise"
GENERATION_PRIORITY = {
    MemoirChapterNodeType.OPENING.value: 0,
    MemoirChapterNodeType.BACKGROUND.value: 1,
    MemoirChapterNodeType.STORY.value: 1,
    MemoirChapterNodeType.REFLECTION.value: 2,
    MemoirChapterNodeType.CLOSING.value: 3,
    MemoirChapterNodeType.TRANSITION.value: 4,
}


def _signature(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(item.strip() for item in value if isinstance(item, str) and item.strip()))


def job_response(job: MemoirEditingJob) -> MemoirDraftBlockJobResponse:
    return MemoirDraftBlockJobResponse.model_validate(job)


async def _load_verified_editorial_contexts(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
    payload: dict[str, object],
) -> list[DraftBlockEditorialContextInput]:
    contexts = await load_chapter_editorial_contexts(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    current_signature = editorial_context_signature(contexts)
    if payload.get("editorial_context_signature") != current_signature:
        raise ValueError(
            "chapter editorial context changed after the generation job was queued"
        )
    return contexts


async def load_draft_generation_job(
    db: AsyncSession,
    *,
    project_id: str,
    job_id: str,
) -> MemoirEditingJob:
    job = await db.scalar(
        select(MemoirEditingJob).where(
            MemoirEditingJob.project_id == project_id,
            MemoirEditingJob.id == job_id,
            MemoirEditingJob.job_type == MemoirEditingJobType.GENERATE_DRAFT_BLOCKS.value,
        )
    )
    if job is None:
        raise ValueError("draft block generation job does not belong to the project")
    return job


async def enqueue_chapter_block_generation(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
    request: MemoirDraftBlockGenerationRequest,
) -> MemoirEditingJob:
    contract, _ = await load_current_contract_nodes(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    editorial_contexts = await load_chapter_editorial_contexts(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    context_signature = editorial_context_signature(editorial_contexts)
    signature_payload = {
        "contract_id": contract.id,
        "node_ids": request.node_ids,
        "ai_model": request.ai_model or "default",
        "editorial_context_signature": context_signature,
    }
    return await enqueue_editing_job(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
        job_type=MemoirEditingJobType.GENERATE_DRAFT_BLOCKS,
        payload_json={
            "mode": JOB_MODE_GENERATE,
            "chapter_id": chapter_id,
            "contract_id": contract.id,
            "node_ids": request.node_ids,
            "ai_model": request.ai_model,
            "editorial_context_signature": context_signature,
            "input_signature": _signature(signature_payload),
            "source": "chapter_workspace",
        },
        dedupe_key=(
            f"generate-draft-blocks:{project_id}:{chapter_id}:"
            f"{_signature(signature_payload)}"
        ),
        replace_completed=True,
        retry_failed=True,
    )


async def enqueue_block_revision(
    db: AsyncSession,
    *,
    project_id: str,
    block_id: str,
    request: MemoirDraftBlockRevisionJobRequest,
) -> MemoirEditingJob:
    block = await draft_block_queries.load_block(db, project_id=project_id, block_id=block_id)
    if block.current_version_id != request.expected_current_version_id:
        raise ValueError("draft block changed concurrently; reload and retry")
    if block.locked:
        raise ValueError("unlock the draft block before requesting a revision")
    editorial_contexts = await load_chapter_editorial_contexts(
        db,
        project_id=project_id,
        chapter_id=block.chapter_id,
    )
    context_signature = editorial_context_signature(editorial_contexts)
    signature_payload = {
        "block_id": block.id,
        "current_version_id": block.current_version_id,
        "feedback": request.feedback,
        "ai_model": request.ai_model or "default",
        "editorial_context_signature": context_signature,
    }
    return await enqueue_editing_job(
        db,
        project_id=project_id,
        chapter_id=block.chapter_id,
        job_type=MemoirEditingJobType.GENERATE_DRAFT_BLOCKS,
        payload_json={
            "mode": JOB_MODE_REVISE,
            "chapter_id": block.chapter_id,
            "block_id": block.id,
            "expected_current_version_id": block.current_version_id,
            "feedback": request.feedback,
            "ai_model": request.ai_model,
            "editorial_context_signature": context_signature,
            "input_signature": _signature(signature_payload),
            "source": "chapter_workspace",
        },
        dedupe_key=(
            f"revise-draft-block:{project_id}:{block.id}:"
            f"{_signature(signature_payload)}"
        ),
        retry_failed=True,
    )


async def _run_initial_generation(
    db: AsyncSession,
    job: MemoirEditingJob,
    *,
    ai_client: AITextClient | None,
) -> EditingJobResult:
    payload = job.payload_json if isinstance(job.payload_json, dict) else {}
    chapter_id = str(payload.get("chapter_id") or job.chapter_id or "").strip()
    contract, nodes = await load_current_contract_nodes(
        db,
        project_id=job.project_id,
        chapter_id=chapter_id,
    )
    if payload.get("contract_id") != contract.id:
        raise ValueError("chapter contract changed after the generation job was queued")
    editorial_contexts = await _load_verified_editorial_contexts(
        db,
        project_id=job.project_id,
        chapter_id=chapter_id,
        payload=payload,
    )
    selected_nodes = select_generation_nodes(
        nodes,
        requested_node_ids=_string_list(payload.get("node_ids")),
    )
    if not selected_nodes:
        raise ValueError("the current chapter direction has no ready nodes")
    existing_blocks = list(
        (
            await db.scalars(
                select(MemoirDraftBlock).where(
                    MemoirDraftBlock.project_id == job.project_id,
                    MemoirDraftBlock.chapter_id == chapter_id,
                    MemoirDraftBlock.node_id.in_([node.id for node in nodes]),
                )
            )
        ).all()
    )
    blocks_by_node: dict[str, MemoirDraftBlock] = {}
    for block in existing_blocks:
        node_id = str(block.node_id)
        if node_id in blocks_by_node:
            raise ValueError("chapter direction has multiple draft blocks for one node")
        blocks_by_node[node_id] = block
    used_orders = set(
        (
            await db.scalars(
                select(MemoirDraftBlock.sort_order).where(
                    MemoirDraftBlock.project_id == job.project_id,
                    MemoirDraftBlock.chapter_id == chapter_id,
                )
            )
        ).all()
    )
    created_block_ids_by_node: dict[str, str] = {}
    updated_block_ids_by_node: dict[str, str] = {}
    skipped_node_ids = [
        node.id
        for node in selected_nodes
        if (block := blocks_by_node.get(node.id)) is not None
        and (
            block.freshness_status == MemoirDraftBlockFreshnessStatus.CURRENT.value
            or block.locked
        )
    ]
    skipped_locked_node_ids = [
        node.id
        for node in selected_nodes
        if (block := blocks_by_node.get(node.id)) is not None
        and block.freshness_status == MemoirDraftBlockFreshnessStatus.STALE.value
        and block.locked
    ]
    nodes_to_generate = [
        node
        for node in selected_nodes
        if (block := blocks_by_node.get(node.id)) is None
        or (
            block.freshness_status == MemoirDraftBlockFreshnessStatus.STALE.value
            and not block.locked
        )
    ]
    if not nodes_to_generate:
        return EditingJobResult(
            result_json={
                "mode": JOB_MODE_GENERATE,
                "chapter_id": chapter_id,
                "contract_id": contract.id,
                "created_block_ids": [],
                "updated_block_ids": [],
                "skipped_node_ids": skipped_node_ids,
                "skipped_locked_node_ids": skipped_locked_node_ids,
                "model": None,
            }
        )
    client = ai_client or get_ai_service(
        str(payload.get("ai_model") or "").strip() or None
    )
    generation_order = sorted(
        nodes_to_generate,
        key=lambda node: (
            GENERATION_PRIORITY[node.node_type],
            node.sort_order,
            node.id,
        ),
    )
    for node in generation_order:
        context = await build_node_generation_context(
            db,
            project_id=job.project_id,
            contract=contract,
            nodes=nodes,
            node=node,
            editorial_contexts=editorial_contexts,
        )
        generated = await generate_draft_block_text(client, context=context.ai_input)
        claims, stories = context.sources_for_claims(generated.used_claim_ids)
        existing_block = blocks_by_node.get(node.id)
        if existing_block is not None:
            if not existing_block.current_version_id:
                raise ValueError("stale draft block has no current version")
            await draft_blocks.append_version(
                db,
                block=existing_block,
                expected_current_version_id=existing_block.current_version_id,
                content=generated.content,
                created_by=MemoirNarrativeActorType.AI,
                claims=claims,
                stories=stories,
                model=getattr(client, "model_name", None),
                prompt_snapshot=generated.prompt_snapshot,
                sources_refreshed=True,
            )
            updated_block_ids_by_node[node.id] = existing_block.id
        else:
            sort_order = node.sort_order * 1000
            while sort_order in used_orders:
                sort_order += 1
            used_orders.add(sort_order)
            block = await draft_blocks.create_block(
                db,
                project_id=job.project_id,
                chapter_id=chapter_id,
                node_id=node.id,
                block_role=context.ai_input.block_role,
                sort_order=sort_order,
                content=generated.content,
                created_by=MemoirNarrativeActorType.AI,
                claims=claims,
                stories=stories,
                model=getattr(client, "model_name", None),
                prompt_snapshot=generated.prompt_snapshot,
            )
            created_block_ids_by_node[node.id] = block.id
    created_block_ids = [
        created_block_ids_by_node[node.id]
        for node in selected_nodes
        if node.id in created_block_ids_by_node
    ]
    updated_block_ids = [
        updated_block_ids_by_node[node.id]
        for node in selected_nodes
        if node.id in updated_block_ids_by_node
    ]
    return EditingJobResult(
        result_json={
            "mode": JOB_MODE_GENERATE,
            "chapter_id": chapter_id,
            "contract_id": contract.id,
            "created_block_ids": created_block_ids,
            "updated_block_ids": updated_block_ids,
            "skipped_node_ids": skipped_node_ids,
            "skipped_locked_node_ids": skipped_locked_node_ids,
            "model": getattr(client, "model_name", None),
        }
    )


async def _run_revision_generation(
    db: AsyncSession,
    job: MemoirEditingJob,
    *,
    ai_client: AITextClient | None,
) -> EditingJobResult:
    payload = job.payload_json if isinstance(job.payload_json, dict) else {}
    block_id = str(payload.get("block_id") or "").strip()
    expected_version_id = str(payload.get("expected_current_version_id") or "").strip()
    block = await draft_block_queries.load_block(
        db,
        project_id=job.project_id,
        block_id=block_id,
    )
    if block.current_version_id != expected_version_id:
        raise ValueError("draft block changed after the revision job was queued")
    if block.locked:
        raise ValueError("locked draft block cannot be revised by an automatic task")
    contract, nodes = await load_current_contract_nodes(
        db,
        project_id=job.project_id,
        chapter_id=block.chapter_id,
    )
    editorial_contexts = await _load_verified_editorial_contexts(
        db,
        project_id=job.project_id,
        chapter_id=block.chapter_id,
        payload=payload,
    )
    node = next((item for item in nodes if item.id == block.node_id), None)
    if node is None:
        raise ValueError("draft block node is not part of the current chapter direction")
    context = await build_node_generation_context(
        db,
        project_id=job.project_id,
        contract=contract,
        nodes=nodes,
        node=node,
        feedback=str(payload.get("feedback") or ""),
        editorial_contexts=editorial_contexts,
    )
    client = ai_client or get_ai_service(
        str(payload.get("ai_model") or "").strip() or None
    )
    generated = await generate_draft_block_text(client, context=context.ai_input)
    claims, stories = context.sources_for_claims(generated.used_claim_ids)
    return EditingJobResult(
        result_json={
            "mode": JOB_MODE_REVISE,
            "chapter_id": block.chapter_id,
            "block_id": block.id,
            "expected_current_version_id": expected_version_id,
            "content": generated.content,
            "used_claim_ids": generated.used_claim_ids,
            "claim_sources": [item.model_dump(mode="json") for item in claims],
            "story_sources": [item.model_dump(mode="json") for item in stories],
            "prompt_snapshot": generated.prompt_snapshot,
            "feedback": str(payload.get("feedback") or ""),
            "model": getattr(client, "model_name", None),
            "editorial_context_signature": payload.get(
                "editorial_context_signature"
            ),
        }
    )


async def run_draft_block_generation_job(
    db: AsyncSession,
    job: MemoirEditingJob,
    *,
    ai_client: AITextClient | None = None,
) -> EditingJobResult:
    payload = job.payload_json if isinstance(job.payload_json, dict) else {}
    mode = str(payload.get("mode") or "").strip()
    if mode == JOB_MODE_GENERATE:
        return await _run_initial_generation(db, job, ai_client=ai_client)
    if mode == JOB_MODE_REVISE:
        return await _run_revision_generation(db, job, ai_client=ai_client)
    raise ValueError("draft block generation job has an invalid mode")


async def apply_generated_revision(
    db: AsyncSession,
    *,
    project_id: str,
    block_id: str,
    request: MemoirDraftBlockRevisionApplyRequest,
) -> MemoirDraftBlock:
    job = await load_draft_generation_job(
        db,
        project_id=project_id,
        job_id=request.generation_job_id,
    )
    if job.status != MemoirEditingJobStatus.SUCCEEDED.value:
        raise ValueError("draft block revision job has not succeeded")
    payload = job.payload_json if isinstance(job.payload_json, dict) else {}
    result = job.result_json if isinstance(job.result_json, dict) else {}
    if payload.get("mode") != JOB_MODE_REVISE or result.get("mode") != JOB_MODE_REVISE:
        raise ValueError("generation job is not a draft block revision")
    if result.get("block_id") != block_id:
        raise ValueError("generation job belongs to a different draft block")
    expected_version_id = str(result.get("expected_current_version_id") or "").strip()
    if request.expected_current_version_id != expected_version_id:
        raise ValueError("revision suggestion was generated from a different block version")
    block = await draft_block_queries.load_block(db, project_id=project_id, block_id=block_id)
    if block.current_version_id != request.expected_current_version_id:
        raise ValueError("draft block changed concurrently; reload and retry")
    contract, nodes = await load_current_contract_nodes(
        db,
        project_id=project_id,
        chapter_id=block.chapter_id,
    )
    editorial_contexts = await _load_verified_editorial_contexts(
        db,
        project_id=project_id,
        chapter_id=block.chapter_id,
        payload=payload,
    )
    node = next((item for item in nodes if item.id == block.node_id), None)
    if node is None:
        raise ValueError("draft block node is not part of the current chapter direction")
    context = await build_node_generation_context(
        db,
        project_id=project_id,
        contract=contract,
        nodes=nodes,
        node=node,
        feedback=str(result.get("feedback") or ""),
        editorial_contexts=editorial_contexts,
    )
    used_claim_ids = _string_list(result.get("used_claim_ids"))
    output = DraftBlockLLMOutput(
        content=str(result.get("content") or ""),
        used_claim_ids=used_claim_ids,
        unsupported_fact_candidates=[],
    )
    validate_generation_output(context.ai_input, output)
    claims, stories = context.sources_for_claims(used_claim_ids)
    await draft_blocks.append_version(
        db,
        block=block,
        expected_current_version_id=request.expected_current_version_id,
        content=output.content,
        created_by=MemoirNarrativeActorType.AI,
        claims=claims,
        stories=stories,
        model=str(result.get("model") or "").strip() or None,
        prompt_snapshot=str(result.get("prompt_snapshot") or ""),
        feedback=str(result.get("feedback") or "").strip() or None,
        sources_refreshed=True,
    )
    return block
