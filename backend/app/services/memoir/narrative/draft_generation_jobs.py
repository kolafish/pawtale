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
from app.domain.memoir.types import MemoirEditingJobType
from app.models.memoir import MemoirEditingJob
from app.models.memoir_narrative import MemoirDraftBlock
from app.services.ai_factory import get_ai_service
from app.services.ai_types import AITextClient
from app.services.memoir.editing_jobs import EditingJobResult, enqueue_editing_job
from app.services.memoir.narrative import draft_blocks
from app.services.memoir.narrative.draft_block_ai import generate_draft_block_text
from app.services.memoir.narrative.draft_generation_context import (
    build_node_generation_context,
    load_current_contract_nodes,
    select_generation_nodes,
)


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


async def enqueue_chapter_block_generation(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
    ai_model: str | None = None,
    editorial_guidance: list[str] | None = None,
    source: str = "chapter_workspace",
) -> MemoirEditingJob:
    contract, _ = await load_current_contract_nodes(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    guidance = list(
        dict.fromkeys(
            item.strip()
            for item in (editorial_guidance or [])
            if item.strip()
        )
    )[:12]
    signature_payload = {
        "contract_id": contract.id,
        "ai_model": ai_model or "default",
        "editorial_guidance": guidance,
    }
    return await enqueue_editing_job(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
        job_type=MemoirEditingJobType.GENERATE_DRAFT_BLOCKS,
        payload_json={
            "chapter_id": chapter_id,
            "contract_id": contract.id,
            "ai_model": ai_model,
            "editorial_guidance": guidance,
            "input_signature": _signature(signature_payload),
            "source": source,
        },
        dedupe_key=(
            f"generate-draft-blocks:{project_id}:{chapter_id}:"
            f"{_signature(signature_payload)}"
        ),
        replace_completed=True,
        retry_failed=True,
    )


async def run_draft_block_generation_job(
    db: AsyncSession,
    job: MemoirEditingJob,
    *,
    ai_client: AITextClient | None = None,
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
    editorial_guidance = _string_list(payload.get("editorial_guidance"))
    editorial_feedback = "\n".join(
        f"- {item}" for item in editorial_guidance
    )
    selected_nodes = select_generation_nodes(nodes)
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
            feedback=editorial_feedback,
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
            "chapter_id": chapter_id,
            "contract_id": contract.id,
            "created_block_ids": created_block_ids,
            "updated_block_ids": updated_block_ids,
            "skipped_node_ids": skipped_node_ids,
            "skipped_locked_node_ids": skipped_locked_node_ids,
            "model": getattr(client, "model_name", None),
        }
    )
