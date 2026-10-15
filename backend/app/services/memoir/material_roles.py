from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.types import MemoirChatContentStatus, MemoirChatMessageRole, MemoirSpeakerRole
from app.models.memoir import MemoirChatMessage, MemoirVoiceUtterance
from app.services.memoir.transcript_projection import current_utterance_text


STORY_CONTENT_GUIDANCE = (
    "按以下顺序判断整条回答是否有可入书的内容："
    "（1）先区分回答本身是在讲往事，还是只在同意访谈员的编辑建议。"
    "仅同意解释、补充说明、调整写法或安排下一步，且没有新增事实或真实感受，是对话回应，不入书；"
    "例如问‘妈妈大名和昵称的区别，要不要在书里再说明一下’，答‘可以说明一下，就这么解释一下就好了’，应排除。"
    "不能因为问题提到了某段往事，就把编辑确认当成对其中事实的确认。"
    "（2）若回答本身补充或明确确认了往事中的事实，即使只有年龄、姓名或地点也必须保留；"
    "例如问‘发大水那年多大’，答‘我那年五岁’，"
    "问‘那位师傅叫什么’，答‘他叫李明’，都不是当前个人资料，不能按第（4）步排除。"
    "这类事实补充即使暂时找不到归属，也只是待归类，不是非故事。"
    "（3）若回答讲了亲历事件、生活经历、具体回忆或自己的真实感受，整条保留，不因同时报资料、同意编辑建议或问题是资料采集而丢弃、切断。"
    "真实感受不要求同时有完整事件；表达思念、留恋、遗憾等也超出了个人资料，必须保留。"
    "（4）整条只含上述编辑确认，或停留在讲述者本人的当前姓名/称呼、当前年龄、性别、籍贯/老家等个人资料（包括一次回答多项），"
    "以及纯寒暄、停顿、暂停请求，才排除；不要为这些内容建立自我介绍或个人资料章节。"
    "没有 interviewer_line 时按回答本身判断。访谈员的问题帮助确定回答的指代，不可把问题中的猜测写成讲述者确认的事实。"
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


async def interviewer_lines(db: AsyncSession, message_ids: Iterable[str | None]) -> dict[str, str]:
    """For each narrator message, the AI interviewer's line right before it, if it was answering one."""
    ids = {message_id for message_id in message_ids if message_id}
    if not ids:
        return {}
    window = {
        "partition_by": MemoirChatMessage.voice_session_id,
        "order_by": (MemoirChatMessage.turn_index, MemoirChatMessage.created_at),
    }
    ordered = (
        select(
            MemoirChatMessage.id,
            func.lag(MemoirChatMessage.role).over(**window).label("previous_role"),
            func.lag(MemoirChatMessage.content_text).over(**window).label("previous_text"),
        )
        .where(MemoirChatMessage.voice_session_id.in_(
            select(MemoirChatMessage.voice_session_id).where(MemoirChatMessage.id.in_(ids))
        ))
        .subquery()
    )
    rows = await db.execute(
        select(ordered.c.id, ordered.c.previous_text).where(
            ordered.c.id.in_(ids),
            ordered.c.previous_role == MemoirChatMessageRole.ASSISTANT.value,
        )
    )
    return {message_id: text.strip() for message_id, text in rows if (text or "").strip()}
