"""Prompt for the AI interviewer's next spoken line.

Free narration and guided interview share one AI interview. The AI mostly listens: while the
narrator is still telling a story it acknowledges and invites them to go on; once a story is told,
they can't remember, or they ask what to talk about, it may offer one declinable direction from
the life topics. Progress facts are only offered at an opening, never in the middle of a story.
"""

from __future__ import annotations

from app.domain.memoir.types import MemoirSessionGoal
from app.models.memoir import MemoirVoiceSession
from app.services.memoir import completion_interviews
from app.services.memoir.interview_book_context import InterviewBookContext, LifeTopic
from app.services.memoir.narrative.frameworks import FrameworkTopicLevel
from app.services.memoir.voice_followup_ai import (
    INTERVIEWER_TURN_OUTPUT_RULES,
    InterviewDialogue,
    InterviewerPersona,
    ProgressPromptContext,
    _has_user_message,
    _recent_context,
)
from app.services.memoir.prompt_variants import (
    DEFAULT_PROMPT_VARIANT,
    PromptVariant,
    normalize_prompt_variant,
    variant_user_instruction,
)


OPENING_TOPIC_COUNT = 3
_LEVEL_LABELS = {
    FrameworkTopicLevel.COVERED: "聊了不少",
    FrameworkTopicLevel.TOUCHED: "聊过一点",
    FrameworkTopicLevel.NOT_STARTED: "还没聊",
}


def build_stream_turn_prompt(
    voice_session: MemoirVoiceSession | InterviewDialogue,
    *,
    has_previous_project_narration: bool,
    activation_context: str,
    regenerate: bool,
    progress_context: ProgressPromptContext,
    persona: InterviewerPersona,
    completion_prompt_context: completion_interviews.CompletionIssuePromptContext | None,
    book_context: InterviewBookContext | None,
    policy_instruction: str = "",
    prompt_variant: str = DEFAULT_PROMPT_VARIANT,
) -> str:
    normalized_variant = normalize_prompt_variant(prompt_variant)
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
    retrieved_history = _retrieved_history_block(activation_context)
    first_narration = not has_previous_project_narration and not book.chapters
    if book.completion_offer is not None:
        opening_offer_context = (
            "讲述者明确说现在不知道讲什么了。"
            if has_user_message
            else "当前是开场，叙述者可以选择回答一个已有的补充问题。"
        )
        return _join(
            _header(host_name, regenerate, normalized_variant),
            f"{opening_offer_context}直接自然地问下面这个补充问题。"
            "只问问题本身；如果他不想回答，接住并允许他继续讲别的。保持 [listen]。",
            f"补充问题：{book.completion_offer.question}",
            f"讲述者资料：{book.narrator}" if book.narrator else "",
            progress_context.prompt_text if progress_context.progress_update is not None else "",
            _book_block(book, opening=False),
            _topics_block(book.topics),
            policy_section,
            dialogue,
            retrieved_history,
        )
    if has_user_message:
        return _join(
            _header(host_name, regenerate, normalized_variant),
            _follow_up_for_variant(normalized_variant),
            _profile_question(book),
            _person_question(book),
            policy_section,
            dialogue,
            retrieved_history,
            _book_block(book, opening=False),
            _topics_block(book.topics),
        )

    progress = progress_context.prompt_text if progress_context.progress_update is not None else ""
    if book.anchor_topic is None and (first_narration or book.profile_questions):
        # Only the greeting differs: all openings use the same candidates and answer exclusions.
        mode = (
            "当前是第一次开场，这本书还没有内容。\n"
            f"用“{host_name}”很短地自我介绍，说明想听他讲讲自己的故事，慢慢说就好。"
            if first_narration else
            "当前是开场，叙述者以前讲过。\n"
            "自然地欢迎回来。"
        )
        mode += (
            "按下面的资料规则问剩下的一项；都已经回答或拒绝了，就请他讲一件自己想讲的往事。"
            "最多问一个问题，问资料时不另提故事方向。"
            if book.profile_questions else "请他讲一件自己想讲的往事，不再问资料。"
        )
        return _join(
            _header(host_name, regenerate, normalized_variant), mode, _profile_question(book, opening=True), progress,
            _book_block(book, opening=True), _topics_block(book.topics), policy_section, dialogue,
            retrieved_history,
        )

    if book.starts_new_story:
        return _join(
            _header(host_name, regenerate, normalized_variant),
            "当前是开场，讲述者主动选择了‘讲个新故事’。简短欢迎，邀请他从自己想起的一件事开始讲。"
            "不要推荐旧章节的补充问题，不要求接着上次讲，不替他选择话题。保持 [listen]。",
            f"讲述者资料：{book.narrator}" if book.narrator else "",
            progress,
            policy_section,
            dialogue,
            retrieved_history,
            _topics_block(book.topics),
        )

    if book.anchor_topic is not None:
        mode = (
            f"当前是开场，叙述者选了“{book.anchor_topic.title}”这个话题，它关心的是：{book.anchor_topic.intent}。\n"
            "用一两句话从这个话题开个头，给一个具体、好回答的入口。"
        )
        topics = _topics_block(book.topics)
    else:
        mode = f"""当前是开场，叙述者以前讲过。
- 自然地欢迎回来，用一句话说书里已经有了什么，只说一两个章节名，不报段数
- 给两个具体方向让他选：
  一是接着上次，围绕“{book.last_chapter_title or "上次那一段"}”这一段人生往下讲，说成一个具体的入口，不要重提上次最后一个细节；
  二是书里还空着的，从“待讲主题”里取第一个，用它的提问方向说成一个具体的入口
- 只问一个问题：今天想从哪个讲起，也可以讲别的"""
        topics = _thin_topics_block(book.topics)
    return _join(
        _header(host_name, regenerate, normalized_variant),
        mode,
        progress,
        _book_block(book, opening=True),
        topics,
        policy_section,
        dialogue,
        retrieved_history,
    )


_FOLLOW_UP = """叙述者自己决定讲什么，你主要是听众。
- 他还在讲（话没说完、故事没讲完），用一句话接住，请他接着说，比如“后来呢？”，不另起问题
- 一件事讲完了、说记不清了，或者问讲什么好，可以从“人生话题”里提一个可以拒绝的新方向，说成具体的入口
- 他说想自己讲、不想被问时，这次后面只接住，不再提新方向"""

_VARIANT_FOLLOW_UP = """讲述者自己决定讲什么，你主要是听众。
- 他还在自然展开、话没有说完时，简短接住，让他继续，不急着提出新问题
- 一件事讲完了、说记不清了，或者需要入口时，再沿已知的人物、事件或场景提出一个具体、可拒绝的问题
- 他说想自己讲、不想被问时，后面主要倾听和简短承接，不主动安排新方向"""


def _follow_up_for_variant(variant: PromptVariant) -> str:
    if variant == "v4":
        return ""
    return _FOLLOW_UP if variant == "v3_improved" else _VARIANT_FOLLOW_UP


_PROFILE_QUESTIONS = {
    "narrator_name": "怎么称呼他，比如“您怎么称呼？”",
    "narrator_age": "他的年纪，比如“您是哪年生的？”",
    "narrator_origin": "他的老家，比如“您老家是哪里的？”",
    "narrator_gender": "他是男是女，比如“冒昧问一句，我该叫您叔叔还是阿姨呀？”",
}


def _profile_question(book: InterviewBookContext, *, opening: bool = False) -> str:
    if not book.profile_questions:
        return ""
    first, *rest = book.profile_questions
    instead = f"就改问：{_PROFILE_QUESTIONS[rest[0]]}（这一项也说了就不问）" if rest else "就不问资料"
    timing = "" if opening else """- 他刚回答完你上一句的问题，还没开始讲一件具体的事：先用半句接住他的回答，再问这一项
- 他正讲着一件事：先听他讲完，这次不问；一件事讲完了，先问这一项，再开启新故事
"""
    history = "\n\n以前的资料问答原文（可能尚未写入资料；只用于识别已回答或拒绝的项，不重新提问确认）：\n" + "\n".join(
        f"AI：{exchange.question}\n叙述者：{exchange.answer}" for exchange in book.profile_answers
    ) if book.profile_answers else ""
    return f"""这次要问的资料：{_PROFILE_QUESTIONS[first]}
{timing}- 同时检查以前的资料问答和本次对话；一句回答可能包含多个资料项，以最新说法为准
- 岁数和出生年份属于同一项年龄资料：已说“今年七十岁”就不能再问“哪年生的”，也不换个问法确认
- 上下文里他本人已经说了这一项，或明确不想说，{instead}
- 他刚说了怎么称呼，接话时就用这个称呼叫他一声（叫错了他会纠正）{history}"""


def _person_question(book: InterviewBookContext) -> str:
    if book.person_question is None:
        return ""
    return f"""还可以顺口问清书里一个人的一件事：{book.person_question.topic}
- 他正讲着一件事时，先听他讲完，这次不问
- 一件事讲完了，就用一句自然的话问这件事，像聊天时顺口问起，不说为什么问；问了这个，这次就不再提新方向
- 上下文里已经问过、他已经说清了，或者说过不记得、不想聊这个人，就不问"""


def _header(host_name: str, regenerate: bool, prompt_variant: PromptVariant) -> str:
    if prompt_variant in {"v4", "v3_plus"}:
        instruction = variant_user_instruction(prompt_variant)
        if regenerate:
            instruction += "\n\n这次是在替换上一句回应，请换一个角度，不要重复上一句。"
        return instruction
    lines = [
        f"请根据下面的访谈上下文，直接生成下一句“{host_name}”要说的话。",
        "",
        "输出要求：",
        INTERVIEWER_TURN_OUTPUT_RULES,
        f"- 你自称“{host_name}”，不要用这个名字称呼叙述者",
        "- 一般 1 到 2 句，35 到 75 个汉字；最多问一个问题，除了问性别，不要用“是……还是……”的二选一问法",
        "- 讲述过程中不要提记录条数、章节数、整理进度",
        "- 不要编造上下文里没有的事；以前讲过的事只能来自“以前讲过的相关原文”和“书里的人物”",
        "- 叙述者明确要换话题、暂停或不想说时，以他最新一句为准",
        "- 称呼、年纪、老家、性别只问“这次要问的资料”那一项；没有要问的资料时，不主动追问这些资料",
    ]
    if regenerate:
        lines.append("- 这次是在替换上一句回应，请换一个角度，不要重复上一句")
    lines.extend(["", variant_user_instruction(prompt_variant)])
    return "\n".join(lines)


def _legacy_header(host_name: str, regenerate: bool) -> str:
    """Output rules for book completion, whose wording tested best offline."""
    regenerate_instruction = "这次是在替换上一句回应，请换一个角度，不要重复上一句。" if regenerate else ""
    return f"""请根据下面的访谈上下文，直接生成下一句“{host_name}”要说的话。

输出要求：
{INTERVIEWER_TURN_OUTPUT_RULES}
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


def _book_block(book: InterviewBookContext, *, opening: bool) -> str:
    chapters = "、".join(f"《{title}》{count} 段讲述" for title, count in book.chapters[:12]) or "还没有章节"
    lines = [f"讲述者：{book.narrator}"] if book.narrator else []
    if book.people:
        lines += ["书里的人物（讲述者以前讲过的人，只用来接上话、称呼对，不据此编事）：", *(f"- {line}" for line in book.people)]
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


def _retrieved_history_block(activation_context: str) -> str:
    return "历史讲述候选（检索结果，仅供参考）：\n" + (
        activation_context or "本轮没有检索到历史讲述候选。"
    )


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
    voice_session: MemoirVoiceSession | InterviewDialogue,
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
