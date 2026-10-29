from __future__ import annotations

from dataclasses import dataclass, replace
import json
from typing import Literal

from fastapi import HTTPException
from pydantic import TypeAdapter
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow_naive
from app.domain.memoir.types import (
    MemoirBookRevisionProposalPhase,
    MemoirBookRevisionProposalStatus,
    MemoirEditingJobStatus,
    MemoirEditingJobType,
)
from app.models.memoir import (
    MemoirBookChapterDraftVersion,
    MemoirEditingJob,
)
from app.schemas.memoir_book_review import (
    MemoirBookRevisionComparison,
    MemoirBookRevisionPlan,
    MemoirBookRevisionPreviewVersion,
    MemoirBookRevisionProposalResponse,
)
from app.schemas.memoir_section_digests import MemoirBodyDigest
from app.services.memoir.title_guidance import title_naming_guidance
from app.services.memoir.material_roles import ANSWER_CONTEXT_GUIDANCE
from app.services.memoir import (
    book_revision_generation,
    book_revision_proposals,
    chapter_generation,
    editing_jobs,
    revision_job_workflow,
)
from app.services.memoir.access import load_memoir_project
from app.services.memoir.project_ai_preferences import select_project_ai_model
from app.services.memoir.semantic_state import semantic_json_signature


SectionRevisionOperation = Literal["plan", "feedback", "draft", "revise"]
_JOB_TYPE = MemoirEditingJobType.GENERATE_CHAPTER_SECTION_REVISION


@dataclass(frozen=True)
class _Target:
    context: chapter_generation.ChapterGenerationContext
    version: MemoirBookChapterDraftVersion
    index: int
    snapshots: list[dict[str, object]]
    section: chapter_generation.ChapterSectionInput
    snapshot: dict[str, object]


def _job_key(project_id: str, chapter_id: str, section_id: str) -> str:
    return f"section-revision:{project_id}:{chapter_id}:{section_id}"


async def _target(
    db: AsyncSession,
    project_id: str,
    chapter_id: str,
    section_id: str,
    *,
    version_id: str | None = None,
    input_signature: str | None = None,
) -> _Target:
    context = await chapter_generation.load_chapter_generation_context(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    index = next((i for i, item in enumerate(context.sections) if item.section_id == section_id), -1)
    if index < 0:
        raise ValueError("小节不存在")
    version = await chapter_generation.latest_chapter_draft_version(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    if version is None:
        raise ValueError("请先生成本章正文")
    if version_id and version.id != version_id:
        raise ValueError("正文已更新，请重新发起修改")
    if input_signature and context.input_signature != input_signature:
        raise ValueError("本章素材已变化，请重新发起修改")
    if not chapter_generation.chapter_draft_is_current(context, version):
        raise ValueError("请先更新有变化的小节正文")
    snapshots = [dict(item) for item in version.section_snapshot_json if isinstance(item, dict)]
    if [str(item.get("section_id") or "") for item in snapshots] != [
        section.section_id for section in context.sections
    ]:
        raise ValueError("本章小节已经变化，请重新发起修改")
    if not str(snapshots[index].get("content") or "").strip():
        raise ValueError("这一小节还没有正文")
    return _Target(
        context,
        version,
        index,
        snapshots,
        context.sections[index],
        snapshots[index],
    )


def _state(job: MemoirEditingJob | None, *, required: bool = True) -> dict[str, object]:
    if job is not None:
        result = dict(job.result_json or {})
        if result.get("base_version_id") and result.get("proposal_phase"):
            return result
        value = (job.payload_json or {}).get("proposal_state")
        if isinstance(value, dict) and value.get("base_version_id") and value.get("proposal_phase"):
            return dict(value)
    if required:
        raise ValueError("修改方案还没有准备好")
    return {}


async def _job(
    db: AsyncSession,
    project_id: str,
    chapter_id: str,
    section_id: str,
) -> MemoirEditingJob | None:
    return await editing_jobs.load_project_editing_job(
        db,
        project_id=project_id,
        job_type=_JOB_TYPE,
        dedupe_key=_job_key(project_id, chapter_id, section_id),
    )


async def _proposal_job(
    db: AsyncSession,
    project_id: str,
    chapter_id: str,
    section_id: str,
    proposal_id: str,
) -> MemoirEditingJob:
    job = await _job(db, project_id, chapter_id, section_id)
    if job is None or job.id != proposal_id or not _state(job, required=False):
        raise ValueError("修改方案不存在")
    return job


def _job_response(
    job: MemoirEditingJob | None,
    project_id: str,
) -> dict[str, object]:
    response = editing_jobs.project_editing_job_state(project_id=project_id, job=job)
    proposal = _state(job, required=False)
    return {
        **response,
        "operation": str((job.payload_json or {}).get("source") or "plan") if job else "plan",
        "proposal_id": job.id if job is not None and proposal else None,
    }


def _source_text(target: _Target) -> str:
    materials = "\n".join(
        (f"对应提问：{material.interviewer_line}\n" if material.interviewer_line else "") + f"素材版本：{material.text}"
        for material in target.section.materials
        if material.text.strip()
    ) or "（没有可用的素材版本）"
    return (
        f"章节：{target.context.chapter_title}\n"
        "本章小节顺序："
        + "、".join(section.title for section in target.context.sections)
        + f"\n目标小节：{target.section.title}\n"
        f"当前正文：\n{str(target.snapshot.get('content') or '').strip()}\n\n"
        f"本节素材版本：\n{materials}"
    )


_PLAN_FORMAT = title_naming_guidance("section") + """请输出严格 JSON：
{
  "summary": "这一轮准备怎样修改的简明概括",
  "items": [{
    "title": "本项修改目标",
    "reason": "为什么需要这样调整",
    "approach": "具体准备怎样调整，但不写修改后的正文",
    "targets": ["目标小节名或具体段落"]
  }],
  "book_metadata_patch": {}
}

只给修改思路，不输出修改后的正文；只修改目标小节；用户明确要求时可以修改标题，
但不改变小节数量、顺序或素材归属；事实只来自当前正文、素材版本及单列的用户原话。旧计划仅描述编辑范围，确认计划不代表确认其中新增的事实；
没有事实依据时明确说明不能添加；book_metadata_patch 必须为空对象。
"""


async def _generate_plan(
    target: _Target,
    feedback: str,
    ai_model: str | None,
    current: MemoirBookRevisionPlan | None = None,
    prior_instructions: list[str] | None = None,
) -> book_revision_generation.GeneratedRevisionPlan:
    if current is None:
        prompt = (
            "请为一个回忆录小节制定可供用户确认的修改思路。\n\n"
            f"用户修改意见：\n{feedback}\n\n{_source_text(target)}\n\n{_PLAN_FORMAT}"
        )
        engine = "llm_chapter_section_revision_plan_v1"
    else:
        prompt = (
            "请根据用户反馈更新完整修改思路，本次范围仅限这个小节。\n\n"
            "当前修改思路：\n"
            f"{json.dumps(current.model_dump(mode='json'), ensure_ascii=False, indent=2)}\n\n"
            f"用户最新意见：\n{feedback}\n\n{_source_text(target)}\n\n"
            f"请返回完整的新思路，不要只返回增量说明。\n{_PLAN_FORMAT}"
        )
        engine = "llm_chapter_section_revision_plan_feedback_v1"
    prompt += "\n" + ANSWER_CONTEXT_GUIDANCE + "\n此前用户原话（按时间排列；新反馈明确纠正时以新反馈为准）：\n" + "\n".join(prior_instructions or [])
    generated = await book_revision_generation.generate_revision_plan(
        project_id=target.context.project_id,
        prompt=prompt,
        engine=engine,
        error_message="生成小节修改思路时遇到问题，请稍后重试",
        ai_model=ai_model,
        ai_client=None,
        reference_maps=(),
    )
    if generated.plan.book_metadata_patch:
        generated = book_revision_generation.GeneratedRevisionPlan(
            plan=generated.plan.model_copy(update={"book_metadata_patch": {}}),
            resolved_ai_model=generated.resolved_ai_model,
            generation=generated.generation,
        )
    return generated


async def _generate_candidate(
    db: AsyncSession,
    target: _Target,
    instruction: str,
    ai_model: str | None,
    *,
    current_content: str | None = None,
    current_title: str | None = None,
) -> chapter_generation.GeneratedChapter:
    previous = target.context.sections[target.index - 1] if target.index else None
    previous_content = (
        str(target.snapshots[target.index - 1].get("content") or "").strip()
        if previous is not None
        else ""
    )
    continuity = await chapter_generation.load_section_continuity(db, context=target.context)
    return await chapter_generation.generate_chapter_section(
        target.context,
        section=replace(target.section, title=current_title or target.section.title),
        chapter_sections=target.context.sections,
        previous_section_content=previous_content,
        preceding_section_context=chapter_generation.previous_section_context(
            section=previous,
            content=previous_content,
            continuity=(continuity.get(previous.section_id) if previous else None),
        ),
        current_section_content=(
            current_content if current_content is not None
            else str(target.snapshot.get("content") or "").strip()
        ),
        revision_instruction=instruction,
        ai_model=ai_model,
    )


async def enqueue_section_revision_job(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    chapter_id: str,
    section_id: str,
    operation: SectionRevisionOperation,
    proposal_id: str | None,
    feedback: str | None,
    ai_model: str | None,
) -> dict[str, object]:
    project = await load_memoir_project(db, project_id, user_id)
    existing = await _job(db, project_id, chapter_id, section_id)
    if existing is not None and existing.status in editing_jobs.ACTIVE_EDITING_JOB_STATUSES:
        return _job_response(existing, project_id)
    model = select_project_ai_model(project, ai_model)
    payload: dict[str, object] = {
        "source": operation,
        "user_id": user_id,
        "section_id": section_id,
        "requested_ai_model": model,
    }
    if operation == "plan" or (operation == "revise" and not proposal_id):
        previous = _state(existing, required=False)
        if (
            existing is not None
            and existing.status == MemoirEditingJobStatus.SUCCEEDED.value
            and previous.get("proposal_status") in {"ready", "stale"}
        ):
            return _job_response(existing, project_id)
        target = await _target(db, project_id, chapter_id, section_id)
        payload.update(
            base_version_id=target.version.id,
            input_signature=target.context.input_signature,
            feedback=(feedback or "").strip(),
        )
    else:
        if existing is None or existing.id != proposal_id:
            raise ValueError("修改方案不存在")
        state = _state(existing)
        response = await section_revision_proposal_response(
            db,
            project_id=project_id,
            chapter_id=chapter_id,
            section_id=section_id,
            proposal_id=existing.id,
        )
        if response.status != MemoirBookRevisionProposalStatus.READY:
            raise ValueError("这个修改方案已经不能继续处理")
        if operation == "draft" and response.phase != MemoirBookRevisionProposalPhase.PLAN_READY:
            raise ValueError("当前修改思路不能生成修改稿")
        if operation == "revise" and response.phase != MemoirBookRevisionProposalPhase.DRAFT_READY:
            raise ValueError("请先生成修改预览")
        if operation in {"feedback", "draft"} and not state.get("revision_plan"):
            raise ValueError("请直接提交修改意见更新预览")
        payload.update(
            proposal_state=state,
            plan_signature=semantic_json_signature(state.get("revision_plan")),
            feedback=(feedback or "").strip(),
        )
    job = await editing_jobs.enqueue_editing_job(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
        job_type=_JOB_TYPE,
        dedupe_key=_job_key(project_id, chapter_id, section_id),
        replace_completed=True,
        payload_json=payload,
    )
    job.result_json = {
        "stage": "queued",
        "requested_ai_model": model,
        "resolved_ai_model": None,
    }
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return _job_response(job, project_id)


async def get_section_revision_job(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    chapter_id: str,
    section_id: str,
) -> dict[str, object]:
    await load_memoir_project(db, project_id, user_id)
    job = await _job(db, project_id, chapter_id, section_id)
    proposal = _state(job, required=False)
    if proposal.get("proposal_status") in {"applied", "dismissed"}:
        job = None
    return _job_response(job, project_id)


async def run_section_revision_job(
    db: AsyncSession,
    job: MemoirEditingJob,
) -> editing_jobs.EditingJobResult:
    async def execute(
        payload: dict[str, object],
        update: revision_job_workflow.ProgressCallback,
    ) -> dict[str, object]:
        operation = str(payload.get("source") or "")
        project_id, chapter_id = job.project_id, str(job.chapter_id or "")
        section_id = str(payload.get("section_id") or "")
        if operation not in {"plan", "feedback", "draft", "revise"}:
            raise ValueError("不支持的小节修改任务")
        await load_memoir_project(db, project_id, str(payload.get("user_id") or ""))
        ai_model = str(payload["requested_ai_model"]) if payload.get("requested_ai_model") else None
        if operation == "revise":
            previous = dict(payload.get("proposal_state") or {})
            if previous and (
                previous.get("proposal_status") != "ready"
                or previous.get("proposal_phase") != "draft_ready"
            ):
                raise ValueError("这版修改预览已经不能继续调整")
            baseline = previous or payload
            version_id = str(baseline.get("base_version_id") or "")
            signature = str(baseline.get("input_signature") or "")
            target = await _target(
                db, project_id, chapter_id, section_id,
                version_id=version_id, input_signature=signature,
            )
            feedback = str(payload.get("feedback") or "").strip()
            if not feedback:
                raise ValueError("请先输入或说出修改意见")
            prior_instructions = TypeAdapter(list[str]).validate_python(
                previous.get("revision_instructions", [])
            )
            instruction = feedback
            if prior_instructions:
                instruction = (
                    "先前用户要求（按时间排列，仅用于理解当前预览及保留用户给出的事实更正，"
                    "不重新执行旧的编辑范围）：\n"
                    + "\n".join(f"{i + 1}. {text}" for i, text in enumerate(prior_instructions))
                    + "\n本次用户要求（本次唯一编辑范围；与先前要求冲突时以本次为准）：\n"
                    + feedback
                )
            await update({"stage": "revising"})
            await db.commit()
            generated = await _generate_candidate(
                db, target, instruction, ai_model,
                current_content=str(previous["candidate_content"]) if previous else None,
                current_title=str(previous["candidate_title"]) if previous else None,
            )
            db.expire_all()
            await _target(
                db, project_id, chapter_id, section_id,
                version_id=version_id, input_signature=signature,
            )
            await update({"stage": "saving"})
            candidate = generated.sections[0]
            result = {
                "proposal_status": "ready",
                "proposal_phase": "draft_ready",
                "base_version_id": version_id,
                "input_signature": signature,
                "chapter_title": target.context.chapter_title,
                "section_title": target.section.title,
                "revision_plan": None,
                "revision_instructions": [*prior_instructions, feedback],
                "candidate_title": candidate.title,
                "candidate_content": candidate.content,
                "candidate_body_digest": candidate.body_digest.model_dump(mode="json"),
                "summary": f"已按要求生成修改预览：{feedback}",
                "requested_ai_model": ai_model,
                "resolved_ai_model": generated.model,
                "generation": {"engine": "llm_chapter_section_revision_direct_v1", "model": generated.model},
                "result_version_id": None,
            }
        elif operation == "plan":
            target = await _target(
                db, project_id, chapter_id, section_id,
                version_id=str(payload.get("base_version_id") or ""),
                input_signature=str(payload.get("input_signature") or ""),
            )
            await update({"stage": "revising"})
            await db.commit()
            generated = await _generate_plan(target, str(payload.get("feedback") or ""), ai_model)
            db.expire_all()
            current = await _target(
                db, project_id, chapter_id, section_id,
                version_id=str(payload.get("base_version_id") or ""),
                input_signature=str(payload.get("input_signature") or ""),
            )
            await update({"stage": "saving"})
            result = {
                "proposal_status": "ready",
                "proposal_phase": "plan_ready",
                "base_version_id": current.version.id,
                "input_signature": current.context.input_signature,
                "chapter_title": current.context.chapter_title,
                "section_title": current.section.title,
                "revision_plan": generated.plan.model_dump(mode="json"),
                "revision_instructions": [str(payload.get("feedback") or "")],
                "candidate_title": "",
                "candidate_content": "",
                "summary": generated.plan.summary.strip(),
                "requested_ai_model": ai_model,
                "resolved_ai_model": generated.resolved_ai_model,
                "generation": generated.generation,
                "result_version_id": None,
            }
        else:
            previous = dict(payload.get("proposal_state") or {})
            if (
                previous.get("proposal_status") != "ready"
                or semantic_json_signature(previous.get("revision_plan"))
                != str(payload.get("plan_signature") or "")
            ):
                raise ValueError("修改思路已经变化，请重新查看")
            target = await _target(
                db, project_id, chapter_id, section_id,
                version_id=str(previous.get("base_version_id") or ""),
                input_signature=str(previous.get("input_signature") or ""),
            )
            prior_instructions = TypeAdapter(list[str]).validate_python(previous.get("revision_instructions", []))
            plan = MemoirBookRevisionPlan.model_validate(previous["revision_plan"])
            await update({"stage": "revising"})
            await db.commit()
            if operation == "feedback":
                generated = await _generate_plan(
                    target,
                    str(payload.get("feedback") or ""),
                    ai_model,
                    current=plan,
                    prior_instructions=prior_instructions,
                )
                patch = {
                    "proposal_phase": "plan_ready",
                    "revision_plan": generated.plan.model_dump(mode="json"),
                    "revision_instructions": [*prior_instructions, str(payload.get("feedback") or "")],
                    "candidate_title": "",
                    "candidate_content": "",
                    "summary": generated.plan.summary.strip(),
                    "resolved_ai_model": generated.resolved_ai_model,
                    "generation": generated.generation,
                }
            else:
                generated = await _generate_candidate(
                    db, target,
                    "请严格按以下已确认的修改思路调整当前小节：\n"
                    + json.dumps(plan.model_dump(mode="json"), ensure_ascii=False)
                    + "\n用户原话（事实来源，按时间排列）：\n" + "\n".join(prior_instructions)
                    + "\n计划仅确定编辑范围；新增事实必须由素材或这些用户原话明确支持。",
                    ai_model,
                )
                candidate = generated.sections[0]
                patch = {
                    "proposal_phase": "draft_ready",
                    "candidate_title": candidate.title,
                    "candidate_content": candidate.content,
                    "candidate_body_digest": candidate.body_digest.model_dump(mode="json"),
                    "summary": "修改稿已按确认思路生成，请对照修改前后内容。",
                    "resolved_ai_model": generated.model,
                    "generation": {
                        "engine": "llm_chapter_section_revision_draft_v1",
                        "model": generated.model,
                    },
                }
            db.expire_all()
            await _target(
                db, project_id, chapter_id, section_id,
                version_id=str(previous.get("base_version_id") or ""),
                input_signature=str(previous.get("input_signature") or ""),
            )
            await update({"stage": "saving"})
            result = {**previous, **patch, "requested_ai_model": ai_model}
        return result

    return await revision_job_workflow.run(db, job, execute)


async def section_revision_proposal_response(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
    section_id: str,
    proposal_id: str,
) -> MemoirBookRevisionProposalResponse:
    proposal = await _proposal_job(db, project_id, chapter_id, section_id, proposal_id)
    values = _state(proposal)
    baseline = await db.get(MemoirBookChapterDraftVersion, str(values.get("base_version_id") or ""))
    if baseline is None or baseline.project_id != project_id:
        raise ValueError("修改前的章节版本已不可用")
    before = next(
        (
            item
            for item in baseline.section_snapshot_json
            if isinstance(item, dict) and item.get("section_id") == section_id
        ),
        None,
    )
    if before is None:
        raise ValueError("修改前的小节已不可用")
    status = MemoirBookRevisionProposalStatus(str(values.get("proposal_status") or "ready"))
    if status == MemoirBookRevisionProposalStatus.READY:
        try:
            await _target(
                db, project_id, chapter_id, section_id,
                version_id=baseline.id,
                input_signature=str(values.get("input_signature") or ""),
            )
        except (HTTPException, ValueError):
            status = MemoirBookRevisionProposalStatus.STALE
    phase = MemoirBookRevisionProposalPhase(str(values.get("proposal_phase") or "plan_ready"))
    before_content = str(before.get("content") or "").strip()
    candidate = str(values.get("candidate_content") or "").strip()
    chapter_title = str(values.get("chapter_title") or "")
    section_title = str(values.get("section_title") or before.get("title") or "")
    candidate_title = str(values.get("candidate_title") or section_title).strip()
    changed = (
        phase == MemoirBookRevisionProposalPhase.DRAFT_READY
        and (candidate != before_content or candidate_title != section_title)
    )

    def snapshot(title: str, content: str) -> list[dict[str, object]]:
        return [{
            "chapter_id": chapter_id,
            "chapter_title": chapter_title,
            "sections": [{
                "section_id": section_id,
                "title": title,
                "content": content,
            }],
        }]

    comparison = MemoirBookRevisionComparison.model_validate({
        **book_revision_proposals.build_comparison(
            before=snapshot(section_title, before_content),
            after=snapshot(
                candidate_title if changed else section_title,
                candidate if changed else before_content,
            ),
        ),
        "metadata_changes": [],
    })
    return MemoirBookRevisionProposalResponse(
        id=proposal.id,
        project_id=project_id,
        status=status,
        phase=phase,
        revision_plan=(
            MemoirBookRevisionPlan.model_validate(values["revision_plan"])
            if values.get("revision_plan") else None
        ),
        summary=str(values.get("summary") or ""),
        base_output_version_id=baseline.id,
        result_output_version_id=(
            str(values["result_version_id"])
            if values.get("result_version_id")
            else None
        ),
        comparison=comparison,
        before=MemoirBookRevisionPreviewVersion(version=baseline.version),
        after=MemoirBookRevisionPreviewVersion(
            version=baseline.version + int(changed)
        ),
        can_apply=(
            status == MemoirBookRevisionProposalStatus.READY
            and changed
            and proposal.status == MemoirEditingJobStatus.SUCCEEDED.value
        ),
        can_dismiss=(
            status
            in {
                MemoirBookRevisionProposalStatus.READY,
                MemoirBookRevisionProposalStatus.STALE,
            }
            and proposal.status not in editing_jobs.ACTIVE_EDITING_JOB_STATUSES
        ),
        created_at=proposal.created_at,
        updated_at=proposal.updated_at,
    )


async def finalize_section_revision(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    chapter_id: str,
    section_id: str,
    proposal_id: str,
    action: Literal["apply", "dismiss"],
) -> MemoirBookRevisionProposalResponse:
    await load_memoir_project(db, project_id, user_id)
    proposal = await _proposal_job(db, project_id, chapter_id, section_id, proposal_id)
    values = _state(proposal)
    if proposal.status in editing_jobs.ACTIVE_EDITING_JOB_STATUSES:
        raise ValueError("请等待当前调整完成")
    if action == "dismiss":
        if values.get("proposal_status") == "applied":
            raise ValueError("已经使用的修改版本不能放弃")
        patch = {"proposal_status": "dismissed"}
    else:
        response = await section_revision_proposal_response(
            db, project_id=project_id, chapter_id=chapter_id,
            section_id=section_id, proposal_id=proposal_id,
        )
        if not response.can_apply:
            raise ValueError("这版修改稿已经不能确认")
        target = await _target(
            db, project_id, chapter_id, section_id,
            version_id=str(values.get("base_version_id") or ""),
            input_signature=str(values.get("input_signature") or ""),
        )
        requested_model = str(values["requested_ai_model"]) if values.get("requested_ai_model") else None
        resolved_model = str(values["resolved_ai_model"]) if values.get("resolved_ai_model") else None
        _, version = await chapter_generation.apply_chapter_section_candidate(
            db,
            project_id=project_id,
            chapter_id=chapter_id,
            section_id=section_id,
            expected_version_id=target.version.id,
            expected_input_signature=target.context.input_signature,
            candidate_title=str(values.get("candidate_title") or target.section.title),
            candidate_content=str(values.get("candidate_content") or ""),
            candidate_body_digest=MemoirBodyDigest.model_validate(values["candidate_body_digest"]) if values.get("candidate_body_digest") else None,
            feedback=str(values.get("summary") or ""),
            requested_ai_model=requested_model,
            resolved_ai_model=resolved_model,
        )
        patch = {"proposal_status": "applied", "result_version_id": version.id}
        from app.services.memoir.automatic_book_draft import schedule_book_draft

        await schedule_book_draft(db, project_id=project_id, source="section_revision", settled=True)
    proposal.result_json = {**values, **patch}
    proposal.updated_at = utcnow_naive()
    db.add(proposal)
    await db.commit()
    await db.refresh(proposal)
    return await section_revision_proposal_response(
        db, project_id=project_id, chapter_id=chapter_id,
        section_id=section_id, proposal_id=proposal_id,
    )
