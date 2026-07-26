from app.services.ai_timeouts import AITextTimeoutProfile, ai_text_timeout_seconds
from app.services.ai_types import AITextClient


STYLE_PROMPTS = {
    "细腻": "文字风格要细腻，注重心理描写和情感刻画",
    "白描": "文字风格要简洁白描，用简练的语言勾勒场景和人物",
    "诗意": "文字风格要诗意唯美，运用优美的比喻和意象",
    "轻松": "文字风格要轻松活泼，语言俏皮有趣",
}

LENGTH_PROMPTS = {
    "短": "修改后控制在200-300字左右",
    "中": "修改后控制在400-600字左右",
    "长": "修改后控制在800-1200字左右",
}


def build_revision_character_section(characters: list[dict]) -> str:
    if not characters:
        return ""

    char_descriptions = []
    for char in characters:
        desc = f"【{char['name']}】"
        if char.get("identity"):
            desc += f" {char['identity']}"
        if char.get("personality"):
            desc += f"，性格：{'、'.join(char['personality'])}"
        if char.get("speaking_style"):
            desc += f"，说话风格：{char['speaking_style']}"
        char_descriptions.append(desc)
    return f"\n角色设定：\n{chr(10).join(char_descriptions)}\n"


async def revise_chapter(
    client: AITextClient,
    original_content: str,
    revision_instruction: str,
    characters: list[dict],
    writing_style: str = "细腻",
    chapter_length: str = "中",
    debug: bool = False,
) -> dict:
    char_section = build_revision_character_section(characters)

    system_prompt = "你是一位专业的文字编辑。"
    user_prompt = f"""请根据修改意见，对以下文本内容进行修改和重写。

要求：
- {STYLE_PROMPTS.get(writing_style, STYLE_PROMPTS['细腻'])}
- {LENGTH_PROMPTS.get(chapter_length, LENGTH_PROMPTS['中'])}
- 保持故事的连贯性和角色性格的一致性
- 直接输出修改后的正文内容，不要添加标题、标记或格式标签
{char_section}
---
原始内容：
{original_content}

修改意见：
{revision_instruction}
---

请直接输出修改后的内容："""

    temp = 0.8
    raw_response = await client._call_llm(
        system_prompt,
        user_prompt,
        temperature=temp,
        max_tokens=3000,
        timeout=ai_text_timeout_seconds(AITextTimeoutProfile.GENERATION),
        return_raw=debug,
    )
    if debug:
        content, response_json = raw_response  # type: ignore[misc]
    else:
        content = raw_response  # type: ignore[assignment]
        response_json = None

    result = {"content": content.strip()}
    if debug:
        result["debug_info"] = client._wrap_debug(
            system_prompt,
            user_prompt,
            temp,
            3000,
            content,
            response_json,
        )
    return result
