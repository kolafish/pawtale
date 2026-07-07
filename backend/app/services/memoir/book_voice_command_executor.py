from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.memoir_book import (
    MemoirBookPlanStructureCommandRequest,
    MemoirBookSourceAssignmentInput,
    MemoirBookSourceAssignmentRequest,
    MemoirBookVoiceCommandExecuteRequest,
    MemoirBookVoiceCommandExecuteResponse,
    MemoirBookVoiceCommandOperationResponse,
)
from app.services.memoir import book_plan_write_workflow as memoir_book_plan_write_workflow
from app.services.memoir import book_source_workflow as memoir_book_source_workflow
from app.services.memoir.book_voice_command_parser import EXECUTABLE_MUTATION_INTENTS


async def execute_confirmed_book_voice_command(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    req: MemoirBookVoiceCommandExecuteRequest,
) -> MemoirBookVoiceCommandExecuteResponse:
    intent = req.intent
    operation = req.operation
    if intent != operation.type:
        raise HTTPException(status_code=400, detail="语音操作类型不一致，请重新识别")
    if intent not in EXECUTABLE_MUTATION_INTENTS:
        raise HTTPException(status_code=400, detail="这个语音操作暂时只能预览")

    if intent == "rename_chapter":
        plan = await memoir_book_plan_write_workflow.apply_book_plan_structure_command(
            db,
            user_id=user_id,
            project_id=project_id,
            req=MemoirBookPlanStructureCommandRequest(
                type="rename_chapter",
                chapter_id=_required_operation_text(operation.chapter_id, "缺少要改名的章节"),
                title=_required_operation_text(operation.new_title, "缺少新的章节名"),
            ),
        )
    elif intent == "merge_chapters":
        plan = await memoir_book_plan_write_workflow.apply_book_plan_structure_command(
            db,
            user_id=user_id,
            project_id=project_id,
            req=MemoirBookPlanStructureCommandRequest(
                type="merge_chapters",
                chapter_id=_required_operation_text(operation.chapter_id, "缺少要合并的章节"),
                target_chapter_id=_required_operation_text(operation.target_chapter_id, "缺少合并目标章节"),
            ),
        )
    elif intent == "move_chapter":
        if not _has_move_target(operation):
            raise HTTPException(status_code=400, detail="缺少章节移动目标")
        plan = await memoir_book_plan_write_workflow.apply_book_plan_structure_command(
            db,
            user_id=user_id,
            project_id=project_id,
            req=MemoirBookPlanStructureCommandRequest(
                type="move_chapter",
                chapter_id=_required_operation_text(operation.chapter_id, "缺少要移动的章节"),
                direction=operation.direction if operation.direction in {"up", "down"} else None,
                target_position=operation.target_position if operation.target_position in {"first", "last"} else None,
                target_index=operation.target_index,
                relative_chapter_id=operation.relative_chapter_id,
                relative_position=operation.relative_position if operation.relative_position in {"before", "after"} else None,
            ),
        )
    elif intent == "assign_source":
        plan = await memoir_book_source_workflow.assign_book_sources_to_chapters(
            db,
            user_id=user_id,
            project_id=project_id,
            req=MemoirBookSourceAssignmentRequest(
                assignments=[
                    MemoirBookSourceAssignmentInput(
                        chapter_id=_required_operation_text(operation.chapter_id, "缺少目标章节"),
                        utterance_ids=[
                            _required_operation_text(operation.utterance_id, "缺少要归入的讲述记录"),
                        ],
                    )
                ],
            ),
        )
    else:
        raise HTTPException(status_code=400, detail="这个语音操作暂时只能预览")

    source_coverage = await memoir_book_source_workflow.get_book_source_coverage(
        db,
        user_id=user_id,
        project_id=project_id,
    )
    return MemoirBookVoiceCommandExecuteResponse(
        intent=intent,
        summary=_executed_summary(intent, operation),
        plan=plan,
        source_coverage=source_coverage,
    )


def _required_operation_text(value: str | None, detail: str) -> str:
    text = (value or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail=detail)
    return text


def _has_move_target(operation: MemoirBookVoiceCommandOperationResponse) -> bool:
    return (
        operation.direction in {"up", "down"}
        or operation.target_position in {"first", "last"}
        or operation.target_index is not None
        or (
            bool(operation.relative_chapter_id)
            and operation.relative_position in {"before", "after"}
        )
    )


def _executed_summary(intent: str, operation: MemoirBookVoiceCommandOperationResponse) -> str:
    if intent == "rename_chapter":
        return f"已把「{operation.chapter_title or '该章节'}」改名为「{operation.new_title or '新章名'}」。"
    if intent == "merge_chapters":
        return (
            f"已把「{operation.chapter_title or '该章节'}」"
            f"合并到「{operation.target_chapter_title or '目标章节'}」。"
        )
    if intent == "move_chapter":
        title = operation.chapter_title or "该章节"
        if operation.direction:
            direction = "上移" if operation.direction == "up" else "下移"
            return f"已把「{title}」{direction}。"
        if operation.target_position == "first":
            return f"已把「{title}」移到第一章。"
        if operation.target_position == "last":
            return f"已把「{title}」移到最后一章。"
        if operation.target_index:
            return f"已把「{title}」移到第 {operation.target_index} 章。"
        if operation.relative_chapter_title and operation.relative_position:
            position = "前面" if operation.relative_position == "before" else "后面"
            return f"已把「{title}」移到「{operation.relative_chapter_title}」{position}。"
        return f"已移动「{title}」。"
    if intent == "assign_source":
        source_position = operation.source_position or operation.source_index or 1
        return f"已把待收录第 {source_position} 条归入「{operation.chapter_title or '目标章节'}」。"
    return "已执行语音修改。"
