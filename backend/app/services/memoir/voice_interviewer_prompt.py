"""Prompt for the AI interviewer's next spoken line.

Each mode gets a different context. Free narration only sees the conversation, and the
AI just acknowledges and invites the narrator to go on. Guided interview also sees who is
narrating, the book overview and the life topics, so it can move to another story or topic
once one is told. Progress facts are only offered at an opening, never in the middle of a story.
"""

from __future__ import annotations

from app.domain.memoir.types import MemoirGuidanceMode, MemoirSessionGoal
from app.models.memoir import MemoirVoiceSession
from app.services.memoir import completion_interviews
from app.services.memoir.interview_book_context import InterviewBookContext, LifeTopic
from app.services.memoir.narrative.frameworks import FrameworkTopicLevel
from app.services.memoir.voice_followup_ai import (
    InterviewerPersona,
    ProgressPromptContext,
    _has_user_message,
    _recent_context,
)


OPENING_TOPIC_COUNT = 3
_LEVEL_LABELS = {
    FrameworkTopicLevel.COVERED: "聊了不少",
    FrameworkTopicLevel.TOUCHED: "聊过一点",
    FrameworkTopicLevel.NOT_STARTED: "还没聊",
}


def build_stream_turn_prompt(
    voice_session: MemoirVoiceSession,
    *,
    has_previous_project_narration: bool,
    activation_context: str,
    regenerate: bool,
    progress_context: ProgressPromptContext,
    persona: InterviewerPersona,
    completion_prompt_context: completion_interviews.CompletionIssuePromptContext | None,
    book_context: InterviewBookContext | None,
    policy_instruction: str = "",
) -> str:
    host_name = persona.host_name
    has_user_message = _has_user_message(list(voice_session.messages))
    policy_section = f"当前受控访谈策略：\n{policy_instruction}" if policy_instruction else ""
    if voice_session.session_goal == MemoirSessionGoal.BOOK_COMPLETION.value:
        return _book_completion_prompt(
            voice_session,
            has_user_message=has_user_message,
            host_name=host_name,
            regenerate=regenerate,
            activation_context=activation_context,
            progress_context=progress_context,
            completion_prompt_context=completion_prompt_context,
            policy_section=policy_section,
        )
    book = book_context or InterviewBookContext(chapters=(), last_chapter_title="", topics=(), anchor_topic=None)
    dialogue = "访谈上下文：\n" + _recent_context(voice_session.messages)
    guided = voice_session.guidance_mode == MemoirGuidanceMode.GUIDED_INTERVIEW.value
    if has_user_message:
        if guided:
            return _guided_follow_up_prompt(
                host_name=host_name,
                regenerate=regenerate,
                book=book,
                dialogue=dialogue,
                activation_context=activation_context,
                policy_section=policy_section,
            )
        return _join(_header(host_name, regenerate), _FREE_FOLLOW_UP, policy_section, dialogue)

    progress = progress_context.prompt_text if progress_context.progress_update is not None else ""
    returning = has_previous_project_narration or bool(book.chapters)
    if not guided:
        if returning:
            last = f"，可以提一句上次讲到“{book.last_chapter_title}”那一段" if book.last_chapter_title else ""
            mode = (
                "当前是自由讲述开场，叙述者以前讲过。\n"
                f"自然地欢迎回来{last}，让他自己选是接着讲，还是讲一件最近想起来的事。不要列话题，不要问细节。"
            )
        else:
            # "Tell me anything" is a blank page for a first-time narrator, so the first question is concrete.
            known = f"已知的讲述者情况：{book.narrator}。\n" if book.narrator else ""
            mode = (
                "当前是自由讲述的第一次开场，这本书还没有内容。\n"
                f"{known}"
                f"用“{host_name}”很短地自我介绍，说明想听他讲讲自己的故事，慢慢说就好；"
                "再用一个几乎人人都能马上答上来的具体问题开个头，比如老家在哪、小时候住在什么地方。只问一个问题。"
            )
        return _join(_header(host_name, regenerate), mode, progress, policy_section, dialogue)

    if book.anchor_topic is not None:
        mode = (
            f"当前是引导访谈开场，叙述者选了“{book.anchor_topic.title}”这个话题，它关心的是：{book.anchor_topic.intent}。\n"
            "用一两句话从这个话题开个头，给一个具体、好回答的入口。"
        )
        topics = _topics_block(book.topics)
    elif returning:
        mode = f"""当前是引导访谈开场，叙述者以前讲过。
- 自然地欢迎回来，用一句话说书里已经有了什么，只说一两个章节名，不报段数
- 给两个具体方向让他选：
  一是接着上次，围绕“{book.last_chapter_title or "上次那一段"}”这一段人生往下讲，说成一个具体的入口，不要重提上次最后一个细节；
  二是书里还空着的，从“待讲主题”里取第一个，用它的提问方向说成一个具体的入口
- 只问一个问题：今天想从哪个讲起，也可以讲别的"""
        topics = _thin_topics_block(book.topics)
    else:
        mode = f"""当前是引导访谈首次开场，这本书还没有内容。
- 用“{host_name}”很短地自我介绍，说明你会一路陪着问，想不起来也没关系
- 从“待讲主题”里按人生时间顺序挑两个，说成具体的起点，比如小时候住的地方、印象最深的家人
- 最后只问一个问题：想先从哪一个讲起，也可以讲别的"""
        topics = _thin_topics_block(book.topics)
    return _join(
        _header(host_name, regenerate),
        mode,
        progress,
        _book_block(book, opening=True),
        topics,
        policy_section,
        dialogue,
    )


_FREE_FOLLOW_UP = """当前是自由讲述：叙述者自己决定讲什么，你是安静的听众，不主导方向。
用一句话接住刚才的内容。他还在讲，就请他接着说，比如“后来呢？”；这件事已经讲完了，就轻轻留个口子，让他想讲什么接着讲。
不问具体细节，不推荐话题。"""


def _header(host_name: str, regenerate: bool) -> str:
    lines = [
        f"请根据下面的访谈上下文，直接生成下一句“{host_name}”要说的话。",
        "",
        "输出要求：",
        "- 只输出要播放的中文口语正文，不要 JSON、Markdown 或解释，不要加说话人标签",
        f"- 你自称“{host_name}”，不要用这个名字称呼叙述者",
        "- 一般 1 到 2 句，35 到 75 个汉字；最多问一个问题，不要用“是……还是……”的二选一问法",
        "- 讲述过程中不要提记录条数、章节数、整理进度",
        "- 不要编造上下文里没有的事；以前讲过的事只能来自“以前讲过的相关原文”",
        "- 叙述者明确要换话题、暂停或不想说时，以他最新一句为准",
    ]
    if regenerate:
        lines.append("- 这次是在替换上一句回应，请换一个角度，不要重复上一句")
    return "\n".join(lines)


def _legacy_header(host_name: str, regenerate: bool) -> str:
    """Output rules for book completion and guided follow-ups, whose wording tested best offline."""
    regenerate_instruction = "这次是在替换上一句回应，请换一个角度，不要重复上一句。" if regenerate else ""
    return f"""请根据下面的访谈上下文，直接生成下一句“{host_name}”要说的话。

输出要求：
- 只输出要播放的中文口语正文，不要 JSON，不要 Markdown，不要解释
- 不要在正文前加“{host_name}：”“老余：”“小余：”“AI：”等说话人标签；自称只能出现在自然句子里，标签不属于可播放内容
- 你可以自主组合动作：承接认可、鼓励继续讲、小结、自然换方向、继续追问，或没听清/太短时温和接住
- 普通回应 1 到 2 句，建议 35 到 75 个汉字
- 问候、进度和问题都由你统一组织成一句或两句连贯口语，不要说成长开场白，也不要出现分段播报感
- 不要上来就追问历史细节，不要给叙述者压力
- 不要机械重复叙述者刚说过的话；认可必须落在具体内容上，但不要把用户原话换个说法再说一遍
- 如果要问问题，一次只问一个自然问题
- 不要编造上下文没有的信息；只有“历史讲述候选”中出现的内容才能作为跨会话事实使用
- 如果用户最新一句明确要求换方向，必须尊重最新一句
{regenerate_instruction}""".rstrip()


def _guided_follow_up_prompt(
    *,
    host_name: str,
    regenerate: bool,
    book: InterviewBookContext,
    dialogue: str,
    activation_context: str,
    policy_section: str,
) -> str:
    anchor = f"，本次话题是“{book.anchor_topic.title}”" if book.anchor_topic else ""
    mode = f"""当前是主题讲述{anchor}；如果最近对话中叙述者已经选择了其他主题，以最近对话为准。
- 如果叙述者明确说想换主题，下一句必须围绕新主题展开，不要继续旧话题
- 优先顺着当前故事的势能继续听或只追一个自然方向，不要为了“完整”机械补齐时间、地点、人物、起因和结果
- 只有确实影响理解时，才询问背景、人物关系、原因、结果、转场或感受
- 不要连续追同一个细节超过两轮；当前受控访谈策略存在时，以它为最高优先级
- 每段人生里都可以聊到重要的人、转折和难处"""
    moving_on = (
        "一个故事讲完了、叙述者说记不清，或者这个话题已经讲得差不多了，就别在原处接着问，"
        "可以换这个话题里的另一个人或另一件事，或者从“人生话题”里提一个可以拒绝的新方向，说成具体的入口。"
        "一次只问一个问题，不要用“是……还是……”。"
    )
    return _join(
        _legacy_header(host_name, regenerate),
        mode,
        policy_section,
        dialogue,
        "历史讲述候选：\n" + (activation_context or "本轮没有检索到历史讲述候选。"),
        _book_block(book, opening=False),
        _topics_block(book.topics),
        moving_on,
    )


def _book_block(book: InterviewBookContext, *, opening: bool) -> str:
    chapters = "、".join(f"《{title}》{count} 段讲述" for title, count in book.chapters[:12]) or "还没有章节"
    lines = [f"讲述者：{book.narrator}"] if book.narrator else []
    lines += ["书稿概况：", chapters]
    if opening and book.last_chapter_title:
        lines.append(f"上次讲到：《{book.last_chapter_title}》")
    return "\n".join(lines)


def _topics_block(topics: tuple[LifeTopic, ...]) -> str:
    levels = []
    for level in (FrameworkTopicLevel.COVERED, FrameworkTopicLevel.TOUCHED, FrameworkTopicLevel.NOT_STARTED):
        titles = "、".join(topic.title for topic in topics if topic.level == level)
        if titles:
            levels.append(f"{_LEVEL_LABELS[level]}：{titles}")
    return "人生话题（按人生先后，不是必须讲完的清单）：" + "；".join(levels) if levels else ""


def _thin_topics_block(topics: tuple[LifeTopic, ...]) -> str:
    # Topics are already in template (roughly life) order, so a stable sort keeps it among equals.
    thinnest = sorted(topics, key=lambda topic: topic.material_count)[:OPENING_TOPIC_COUNT]
    lines = ["待讲主题（书里最少的几项，可以从这里找新方向，不是必须完成的清单）："]
    for topic in thinnest:
        state = "还没讲过" if topic.material_count == 0 else f"讲过 {topic.material_count} 段"
        lines.append(f"  · {topic.title}（{state}）：{topic.intent}")
    return "\n".join(lines)


def _join(*parts: str) -> str:
    return "\n\n".join(part for part in parts if part.strip())


def _book_completion_prompt(
    voice_session: MemoirVoiceSession,
    *,
    has_user_message: bool,
    host_name: str,
    regenerate: bool,
    activation_context: str,
    progress_context: ProgressPromptContext,
    completion_prompt_context: completion_interviews.CompletionIssuePromptContext | None,
    policy_section: str,
) -> str:
    if has_user_message:
        mode_instruction = """当前是成书补访，只围绕“当前补全问题”推进。
- 先承接叙述者刚才真正回答到的内容，用一句话感谢并说明这一段已经保存
- 不再继续追问细节，也不要自动切换到下一题；后台会核对内容，用户随后回到全书概览自行选择
- 如果回答跑题，先自然接住，再温和带回当前唯一目标
- 不要重复询问已经列为“不要重复追问”的事实"""
    elif completion_prompt_context is not None:
        mode_instruction = f"""当前是成书补访首次开场。
- 最多两句，第一句简短说明今天只补一件事
- 第二句自然地提出这个具体问题：{completion_prompt_context.question}
- 不要展示任务 ID，不要念系统说明，不要扩展成多个问题"""
    else:
        mode_instruction = """当前成书补访已经没有待回答的问题。
- 简短说明本轮已经结束，请用户返回全书概览
- 不要继续提问，也不要引导用户再次录音"""
    completion = (
        completion_prompt_context.prompt_text
        if completion_prompt_context
        else "本轮没有当前补全问题，按当前引导模式继续。"
    )
    return _join(
        _legacy_header(host_name, regenerate),
        "项目进度上下文：\n" + progress_context.prompt_text,
        mode_instruction,
        policy_section,
        "当前补全问题：\n" + completion,
        "访谈上下文：\n" + _recent_context(voice_session.messages),
        "历史讲述候选：\n" + (activation_context or "本轮没有检索到历史讲述候选。"),
    )
