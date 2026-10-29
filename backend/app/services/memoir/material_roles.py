from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import and_, func, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.domain.memoir.types import MemoirChatContentStatus, MemoirChatMessageRole, MemoirSpeakerRole
from app.models.memoir import MemoirChatMessage, MemoirVoiceUtterance
from app.services.memoir.transcript_projection import current_utterance_text


ANSWER_CONTEXT_GUIDANCE = (
    "对应提问只用于理解回答，不能把问题中的猜测当成事实。"
    "可结合明确回答补出其省略的主体、对象或事件，使内容独立可懂；保留否定、不确定性、转述者和人物视角。"
    "短回答的主题跟随其对应提问，不按相邻素材或书名猜测。"
    "确定与不确定只作用于各自事实，不把记不清年份扩散成年龄也不确定。"
    "对复合问题含糊地回答‘是’，不等于逐项确认；编辑建议获同意也不等于新增事实获确认。"
    "无法消解的指代不猜人物身份，没有对应提问就只按讲述本身判断。"
)
STORY_THEME_GUIDANCE = (
    "章是大主题，小节是章内的小主题；主题可围绕人物、生活领域、地点、阶段或相关经历，"
    "不强制按人生阶段编排，也不要求一件事单独一节。主题连贯的多件事可以合节；"
    "整条素材不可拆分或重复收录，包含多个主题时按整体主要内容安放，不靠标题罗列主题来代替合理归类。"
    "保留合理的既有结构和标题，只为明确的主题问题调整，不为凑数量或追求整齐重排。"
)
STORY_CONTENT_POLICY_VERSION = 1
STORY_CONTENT_GUIDANCE = (
    "逐条按回答本身判断内容角色，再给故事归类；资料采集的问题或本批其他回答不能代替判断。"
    "先检查整条是否提供或明确确认了往事事实、个人经历或真实感受，讲述对象可以是本人或他人。"
    "有这些内容就完整保留，即使很短或同时包含当前个人资料、寒暄及编辑确认；"
    "往事中的年龄、姓名、地点补充同样可入书，暂时找不到位置只是待归类，不是非故事。"
    "只有整条停留在当前本人资料、寒暄、停顿、暂停请求或对编辑建议/下一步安排的同意时才排除，"
    "不建立自我介绍或资料章节，也不因提问含往事而把纯编辑确认当成事实确认。"
    + ANSWER_CONTEXT_GUIDANCE
)



def is_narrator_material(utterance: MemoirVoiceUtterance) -> bool:
    if utterance.chat_message is not None:
        return utterance.chat_message.role == MemoirChatMessageRole.USER.value
    payload = (
        utterance.provider_payload_json
        if isinstance(utterance.provider_payload_json, dict)
        else {}
    )
    return payload.get("speaker_role") != MemoirSpeakerRole.INTERVIEWER.value


def is_countable_material(
    utterance: MemoirVoiceUtterance,
    text: str | None = None,
) -> bool:
    content = (
        text
        if text is not None
        else current_utterance_text(utterance)
    )
    return bool(str(content or "").strip()) and is_narrator_material(utterance)


def narrator_material_clause():
    unlinked_speaker_role = (
        MemoirVoiceUtterance.provider_payload_json["speaker_role"].as_string()
    )
    return or_(
        MemoirVoiceUtterance.chat_message.has(
            MemoirChatMessage.role == MemoirChatMessageRole.USER.value
        ),
        and_(
            MemoirVoiceUtterance.chat_message_id.is_(None),
            func.coalesce(
                unlinked_speaker_role,
                MemoirSpeakerRole.NARRATOR.value,
            )
            != MemoirSpeakerRole.INTERVIEWER.value,
        ),
    )


def is_ready_story_material(utterance: MemoirVoiceUtterance) -> bool:
    message = utterance.chat_message
    return is_countable_material(utterance) and (
        message is None or message.content_status in {
            MemoirChatContentStatus.FINAL.value, MemoirChatContentStatus.CORRECTED.value,
        }
    )


async def interviewer_messages(
    db: AsyncSession, message_ids: Iterable[str | None], *, lock: bool = False,
) -> dict[str, MemoirChatMessage]:
    """Honor edited question bindings, else find the preceding interviewer across answer segments."""
    ids = {message_id for message_id in message_ids if message_id}
    if not ids:
        return {}
    if lock:
        # Lock answers and possible questions together in a stable order. A
        # question edit/rebinding must not race final validation and writing.
        sessions = select(MemoirChatMessage.voice_session_id).where(MemoirChatMessage.id.in_(ids))
        await db.execute(select(MemoirChatMessage).where(or_(
            MemoirChatMessage.id.in_(ids),
            and_(MemoirChatMessage.voice_session_id.in_(sessions), MemoirChatMessage.role.in_([
                MemoirChatMessageRole.ASSISTANT.value, MemoirChatMessageRole.INTERVIEWER.value,
            ])),
        )).order_by(MemoirChatMessage.id).with_for_update().execution_options(populate_existing=True))
    answer = aliased(MemoirChatMessage)
    candidate = aliased(MemoirChatMessage)
    eligible = (
        candidate.voice_session_id == answer.voice_session_id,
        candidate.project_id == answer.project_id,
        candidate.role.in_([MemoirChatMessageRole.ASSISTANT.value, MemoirChatMessageRole.INTERVIEWER.value]),
        candidate.guidance_intent.is_distinct_from("no_answer_prompt"),
        func.trim(candidate.content_text) != "",
    )
    parent = select(candidate.id).where(
        *eligible, candidate.id == answer.parent_message_id,
    ).correlate(answer).scalar_subquery()
    previous = select(candidate.id).where(
        *eligible,
        tuple_(candidate.turn_index, candidate.created_at, candidate.id)
        < tuple_(answer.turn_index, answer.created_at, answer.id),
        # A user may explicitly detach an answer in the interview editor.
        or_(answer.parent_message_id.is_not(None), answer.metadata_json["relation_updated_at"].as_string().is_(None)),
    ).order_by(
        candidate.turn_index.desc(), candidate.created_at.desc(), candidate.id.desc(),
    ).limit(1).correlate(answer).scalar_subquery()
    rows = await db.execute(select(answer.id, MemoirChatMessage).join(
        MemoirChatMessage, MemoirChatMessage.id == func.coalesce(parent, previous),
    ).where(answer.id.in_(ids), answer.role == MemoirChatMessageRole.USER.value)
      .execution_options(populate_existing=True))
    return dict(rows.all())


async def interviewer_lines(
    db: AsyncSession, message_ids: Iterable[str | None], *, lock: bool = False,
) -> dict[str, str]:
    return {
        message_id: message.content_text.strip()
        for message_id, message in (await interviewer_messages(db, message_ids, lock=lock)).items()
    }


def interviewer_context_signature(message: MemoirChatMessage, question: MemoirChatMessage | None) -> str:
    from app.services.memoir.semantic_state import semantic_json_signature
    if question is None and message.parent_message_id is None and not (message.metadata_json or {}).get("relation_updated_at"):
        return ""
    return semantic_json_signature({
        "parent": message.parent_message_id,
        "question_id": question.id if question else None,
        "question": question.content_text.strip() if question else "",
        "person_asked": (question.metadata_json or {}).get("person_asked") if question else None,
    })
