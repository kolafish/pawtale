import httpx
import json
import re
from app.config import get_settings
from app.services.ai_base import AITextServiceBase
from app.services.ai_debug import build_debug_info

settings = get_settings()


class ClaudeTextService(AITextServiceBase):
    """Claude 文本生成服务"""

    def __init__(self, model: str = "claude-opus-4"):
        self.auth_token = settings.anthropic_auth_token
        self.base_url = settings.anthropic_base_url
        # 模型映射（使用代理支持的短格式 model ID）
        model_map = {
            "claude-opus-4": "claude-opus-4-6",
            "claude-sonnet-4": "claude-sonnet-4-6",
        }
        self.model = model_map.get(model, "claude-opus-4-6")

    async def _call_claude(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 4000,
        return_raw: bool = False,
    ) -> str | tuple[str, dict]:
        """调用Claude API（通过 OpenAI 兼容格式）"""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.auth_token}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                },
                timeout=90.0,
            )
            response.raise_for_status()
            result = response.json()

        # 兼容 OpenAI 与 Anthropic 返回格式
        content = self._extract_response_text(result)
        if not content:
            raise ValueError("Claude 返回内容为空或格式无法解析")

        if return_raw:
            return content, result
        return content

    def _extract_response_text(self, result: dict) -> str:
        """兼容不同代理/网关返回的 Claude 内容格式"""
        # OpenAI 兼容格式：choices[0].message.content
        choices = result.get("choices", [])
        if isinstance(choices, list) and choices:
            first_choice = choices[0] if isinstance(choices[0], dict) else {}
            message = first_choice.get("message", {}) if isinstance(first_choice, dict) else {}
            raw_content = message.get("content") if isinstance(message, dict) else None
            content = self._normalize_message_content(raw_content)
            if content:
                return content

            # 兼容少数网关把文本放在 choices[0].text
            text = first_choice.get("text") if isinstance(first_choice, dict) else None
            if isinstance(text, str) and text.strip():
                return text.strip()

        # Anthropic 原生格式：content: [{type:'text', text:'...'}]
        content_blocks = result.get("content")
        if isinstance(content_blocks, list):
            parts: list[str] = []
            for block in content_blocks:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type")
                text = block.get("text")
                if isinstance(text, str) and text.strip() and block_type in (None, "text"):
                    parts.append(text.strip())
            if parts:
                return "\n".join(parts).strip()

        # 兜底：content 是纯字符串
        if isinstance(content_blocks, str) and content_blocks.strip():
            return content_blocks.strip()

        return ""

    def _normalize_message_content(self, raw_content: str | list | dict | None) -> str:
        if isinstance(raw_content, str):
            return raw_content.strip()
        if isinstance(raw_content, list):
            parts: list[str] = []
            for item in raw_content:
                if isinstance(item, str) and item.strip():
                    parts.append(item.strip())
                    continue
                if isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str) and text.strip():
                        parts.append(text.strip())
            return "\n".join(parts).strip()
        if isinstance(raw_content, dict):
            text = raw_content.get("text")
            if isinstance(text, str):
                return text.strip()
        return ""

    async def auto_complete_character(
        self,
        name: str | None = None,
        gender: str | None = None,
        inspiration: str | None = None,
    ) -> dict:
        """根据已有信息自动补全角色设定"""
        system_prompt = """你是一个专业的角色设定师，擅长创建言情小说中的角色。
请根据用户提供的信息，补全角色的其他设定。

输出必须是严格的 JSON 格式，包含以下字段：
{
  "gender": "男/女",
  "age": 数字,
  "identity": "身份/职业",
  "appearance": "外貌描述，包括身高、发型、五官特点等",
  "personality": ["性格标签1", "性格标签2", "性格标签3"],
  "speaking_style": "说话风格描述",
  "background": "简短的背景故事"
}

性格标签请从以下选项中选择：温柔、高冷、傲娇、腹黑、阳光、忠犬、病娇、天然呆、毒舌、闷骚、霸总、奶狗、狼狗、禁欲系

注意：
1. 如果用户提供了灵感来源（如某个影视剧角色），请参考该角色的特点，但不要完全复制
2. 外貌描述要具体，便于后续生成图片
3. 背景故事要简洁，50字以内
4. 只输出 JSON，不要有其他内容"""

        user_parts = []
        if name:
            user_parts.append(f"角色名：{name}")
        if gender:
            user_parts.append(f"性别：{gender}")
        if inspiration:
            user_parts.append(f"灵感来源：{inspiration}（请参考这个角色的特点）")

        if not user_parts:
            user_parts.append("请创建一个适合言情小说的男主角色")

        user_prompt = "\n".join(user_parts)

        content = await self._call_claude(system_prompt, user_prompt, temperature=0.7, max_tokens=1000)

        # 解析 JSON
        try:
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]
            return json.loads(content.strip())
        except json.JSONDecodeError:
            return {
                "gender": gender or "男",
                "age": 28,
                "identity": "公司高管",
                "appearance": "身高180cm，短发，五官深邃",
                "personality": ["高冷", "深情"],
                "speaking_style": "言简意赅，偶尔毒舌",
                "background": "商业精英，事业有成",
            }

    async def generate_story(
        self,
        characters: list[dict],
        scene: dict,
        mood: str,
        writing_style: str = "细腻",
        chapter_length: str = "中",
        previous_content: str | None = None,
        custom_direction: str | None = None,
        chapter_number: int = 1,
        outline_context: dict | None = None,
        story_state: dict | None = None,
        debug: bool = False,
    ) -> dict:
        """生成故事内容和分支选项"""
        # 构建角色描述
        char_descriptions = []
        for char in characters:
            desc = f"【{char['name']}】"
            if char.get('identity'):
                desc += f" {char['identity']}"
            if char.get('personality'):
                desc += f"，性格：{'、'.join(char['personality'])}"
            if char.get('speaking_style'):
                desc += f"，说话风格：{char['speaking_style']}"
            if char.get('background'):
                desc += f"，背景：{char['background']}"
            if char.get('core_trait'):
                desc += f"，核心驱动力：{char['core_trait']}"
            if char.get('fatal_flaw'):
                desc += f"，弱点：{char['fatal_flaw']}"
            char_descriptions.append(desc)

        # 构建场景描述
        scene_desc = f"场景：{scene['name']}\n{scene['description']}"
        if scene.get('location'):
            scene_desc += f"\n地点：{scene['location']}"
        if scene.get('time_weather'):
            scene_desc += f"\n时间/天气：{scene['time_weather']}"
        if scene.get('sensory_details'):
            sensory = scene['sensory_details']
            if sensory.get('visual'):
                scene_desc += f"\n视觉：{sensory['visual']}"
            if sensory.get('auditory'):
                scene_desc += f"\n听觉：{sensory['auditory']}"
            if sensory.get('olfactory'):
                scene_desc += f"\n气味：{sensory['olfactory']}"
        if scene.get('props'):
            scene_desc += f"\n可用道具/元素：{'、'.join(scene['props'])}"

        # 判断故事阶段
        story_stage = self._get_story_stage(chapter_number)

        # 构建风格指导
        style_guide = self._build_style_guide(writing_style, mood, story_stage)

        # 章节长度映射
        length_config = {
            "短": {"words": "300-400", "desc": "简洁精炼，聚焦核心情节"},
            "中": {"words": "500-700", "desc": "详略得当，情节与情感并重"},
            "长": {"words": "900-1200", "desc": "充分展开，细腻描写"},
        }
        length_info = length_config.get(chapter_length, length_config["中"])

        # 构建系统提示词
        system_prompt = f"""你是一位资深的言情小说作家，擅长写有灵魂的情感故事。

## 你的写作原则

### 文字风格
{style_guide}

### 字数要求
本章控制在 {length_info['words']} 字，{length_info['desc']}。

### 故事阶段
当前是第 {chapter_number} 章，处于【{story_stage['name']}】阶段。
{story_stage['guidance']}

### 写作禁忌（非常重要）
1. 禁止连续使用叠词（如"美滋滋""气鼓鼓""乐呵呵"），整章最多出现1次
2. 禁止每段都是"动作+对话"的固定模式，要有变化
3. 禁止角色情绪突变，情感转变需要铺垫
4. 禁止对话过于功能性——真人说话会有停顿、重复、未说完的话
5. 禁止过度使用形容词堆砌，用具体动作和细节代替
6. 禁止章节开头直接承接上章结尾动作，需要有情绪/环境过渡

## 输出格式
【章节标题】
（4-8字，有意境，能概括本章情感或转折）

【故事内容】
（正文）

【分支选项】
A. 选项描述（10-20字，暗示不同走向）
B. 选项描述
C. 选项描述（可选）"""

        # 构建用户提示词
        user_prompt = f"""## 角色设定
{chr(10).join(char_descriptions)}

## 场景
{scene_desc}

## 情绪基调：{mood}
"""

        # 添加大纲上下文
        if outline_context:
            user_prompt += "\n## 故事大纲\n"
            if outline_context.get('central_conflict'):
                user_prompt += f"核心冲突：{outline_context['central_conflict']}\n"
            if outline_context.get('ending_type'):
                ending_map = {'HE': '幸福结局', 'BE': '悲剧结局', 'OE': '开放结局'}
                user_prompt += f"结局走向：{ending_map.get(outline_context['ending_type'], outline_context['ending_type'])}\n"
            if outline_context.get('plot_hints'):
                user_prompt += f"剧情提示：{' → '.join(outline_context['plot_hints'])}\n"
            if outline_context.get('forbidden_elements'):
                user_prompt += f"禁止元素：{'、'.join(outline_context['forbidden_elements'])}\n"

        # 添加前情和续写指导
        if previous_content:
            user_prompt += f"\n## 前情提要\n{previous_content}\n"
            user_prompt += "\n## 续写要求\n请自然承接上文，注意开头要有过渡。\n"
            if custom_direction:
                user_prompt += f"\n用户希望的发展方向：{custom_direction}\n"
        else:
            user_prompt += "\n## 开篇要求\n这是故事的开篇，需要快速建立场景氛围并引出主要角色。\n"
            if custom_direction:
                user_prompt += f"\n用户希望的开篇方向：{custom_direction}\n"

        raw_response = await self._call_claude(
            system_prompt,
            user_prompt,
            temperature=0.85,
            max_tokens=3000,
            return_raw=debug,
        )
        if debug:
            content, response_json = raw_response  # type: ignore[misc]
        else:
            content = raw_response  # type: ignore[assignment]
            response_json = None

        parsed_result = self._parse_story_response(content)
        if debug:
            parsed_result["debug_info"] = build_debug_info(
                request_info={
                    "provider": "claude",
                    "model": self.model,
                    "temperature": 0.85,
                    "max_tokens": 3000,
                    "system_prompt": system_prompt,
                    "user_prompt": user_prompt,
                },
                response_text=content,
                response_json=response_json,
            )
        return parsed_result

    def _get_story_stage(self, chapter_number: int) -> dict:
        """根据章节数判断故事阶段"""
        if chapter_number == 1:
            return {"name": "开篇", "guidance": "重点是建立人物形象和初始关系，制造第一个吸引点。"}
        elif chapter_number <= 3:
            return {"name": "铺垫期", "guidance": "逐步展开人物性格，制造小冲突或暧昧时刻。"}
        elif chapter_number <= 6:
            return {"name": "发展期", "guidance": "关系开始变化，可以有误会、靠近、或小高潮。"}
        elif chapter_number <= 9:
            return {"name": "高潮期", "guidance": "核心冲突爆发或关系重大转折。"}
        else:
            return {"name": "收尾期", "guidance": "回收伏笔，解决冲突，给读者情感满足。"}

    def _build_style_guide(self, writing_style: str, mood: str, story_stage: dict) -> str:
        """构建具体的风格指导"""
        style_rules = {
            "细腻": "注重内心戏和微表情，情感层次丰富，长短句交替。",
            "白描": "克制修饰，精准动词，对话为主，冷静叙述。",
            "诗意": "意象营造，节奏韵律，通感手法，氛围优先。",
            "轻松": "语言俏皮，吐槽视角，节奏明快，对话为王。",
        }
        mood_additions = {
            "甜": "\n甜度把控：心动瞬间要有铺垫。",
            "虐": "\n虐要有因：误会和冲突要有合理原因。",
            "搞笑": "\n笑点自然：从情境和人物性格中来。",
            "治愈": "\n温暖不腻：用日常小事传递温暖。",
        }
        return style_rules.get(writing_style, style_rules["细腻"]) + mood_additions.get(mood, "")

    async def generate_chapter_title(self, content: str) -> str:
        """为已有内容生成章节标题"""
        system_prompt = """你是一个专业的言情小说编辑。
请根据给定的故事内容，生成一个有意境的章节标题。
标题要求：4-8个字，能概括本章主要内容或情感，有文学性。
只输出标题，不要有其他内容。"""

        content = await self._call_claude(system_prompt, f"故事内容：\n{content[:500]}", temperature=0.7, max_tokens=50)
        return content.strip()

    async def revise_chapter(
        self,
        original_content: str,
        revision_instruction: str,
        characters: list[dict],
        writing_style: str = "细腻",
        chapter_length: str = "中",
        debug: bool = False,
    ) -> dict:
        """根据修改意见重新生成章节内容"""
        style_prompts = {
            "细腻": "文字风格要细腻，注重心理描写和情感刻画。",
            "白描": "文字风格要简洁白描，用简练的语言勾勒场景和人物。",
            "诗意": "文字风格要诗意唯美，运用优美的比喻和意象。",
            "轻松": "文字风格要轻松活泼，语言俏皮有趣。",
        }

        length_prompts = {
            "短": "请控制在200-300字左右。",
            "中": "请控制在400-600字左右。",
            "长": "请控制在800-1200字左右。",
        }

        char_descriptions = []
        for char in characters:
            desc = f"【{char['name']}】"
            if char.get('identity'):
                desc += f" {char['identity']}"
            if char.get('personality'):
                desc += f"，性格：{'、'.join(char['personality'])}"
            char_descriptions.append(desc)

        system_prompt = f"""你是一个专业的言情小说作家和编辑。
用户会给你一段原始故事内容和修改意见，请根据修改意见重新生成这段内容。

{style_prompts.get(writing_style, style_prompts['细腻'])}
{length_prompts.get(chapter_length, length_prompts['中'])}

角色设定：
{chr(10).join(char_descriptions)}

输出格式：
【章节标题】
（标题）

【故事内容】
（正文）

【分支选项】
A. 选项描述
B. 选项描述"""

        user_prompt = f"""原始内容：
{original_content}

修改意见：
{revision_instruction}

请根据修改意见重新生成这段内容。"""

        raw_response = await self._call_claude(
            system_prompt,
            user_prompt,
            temperature=0.8,
            max_tokens=3000,
            return_raw=debug,
        )
        if debug:
            content, response_json = raw_response  # type: ignore[misc]
        else:
            content = raw_response  # type: ignore[assignment]
            response_json = None

        parsed_result = self._parse_story_response(content)
        if debug:
            parsed_result["debug_info"] = build_debug_info(
                request_info={
                    "provider": "claude",
                    "model": self.model,
                    "temperature": 0.8,
                    "max_tokens": 3000,
                    "system_prompt": system_prompt,
                    "user_prompt": user_prompt,
                },
                response_text=content,
                response_json=response_json,
            )
        return parsed_result

    def _parse_story_response(self, content: str) -> dict:
        """解析 AI 返回的故事内容和分支选项"""
        story_content = ""
        chapter_title = None
        choices = []

        if "【章节标题】" in content:
            title_parts = content.split("【章节标题】")
            if len(title_parts) > 1:
                title_section = title_parts[1].split("【")[0].strip()
                chapter_title = title_section.split("\n")[0].strip()

        if "【分支选项】" in content:
            parts = content.split("【分支选项】")
            story_part = parts[0]
            choices_part = parts[1] if len(parts) > 1 else ""
        elif "【选择】" in content:
            parts = content.split("【选择】")
            story_part = parts[0]
            choices_part = parts[1] if len(parts) > 1 else ""
        else:
            story_part = content
            choices_part = ""

        story_content = story_part.replace("【故事内容】", "").replace("【章节标题】", "")
        if chapter_title:
            story_content = story_content.replace(chapter_title, "", 1)
        story_content = story_content.strip()

        if choices_part:
            lines = choices_part.strip().split("\n")
            for line in lines:
                line = line.strip()
                if line and (line.startswith("A.") or line.startswith("B.") or line.startswith("C.")):
                    label = line[0]
                    text = line[2:].strip()
                    choices.append({"label": f"{label}. {text}", "next_node": None})

        if not choices:
            choices = [
                {"label": "A. 继续发展", "next_node": None},
                {"label": "B. 出现转折", "next_node": None},
            ]

        return {
            "title": chapter_title,
            "content": story_content,
            "word_count": len(story_content),
            "choices": choices,
        }

    async def enhance_outline(
        self,
        title: str,
        mood: str | None = None,
        characters: list[dict] | None = None,
        scenes: list[dict] | None = None,
    ) -> dict:
        """根据大纲标题和已有信息，AI生成核心冲突、剧情提示等"""
        system_prompt = """你是一个专业的言情小说策划师，擅长设计引人入胜的故事大纲。
请根据用户提供的信息，生成故事的核心冲突和剧情提示。

输出必须是严格的 JSON 格式：
{
  "central_conflict": "核心冲突描述（50-100字）",
  "plot_hints": ["剧情提示1", "剧情提示2", "剧情提示3"],
  "suggested_mood": "建议的情绪基调（甜/虐/搞笑/治愈）",
  "suggested_ending": "建议的结局类型（HE/BE/OE）"
}

只输出 JSON，不要有其他内容。"""

        user_parts = [f"故事标题/主题：{title}"]
        if mood:
            user_parts.append(f"期望的情绪基调：{mood}")
        if characters:
            char_desc = []
            for c in characters:
                desc = f"- {c.get('name', '未命名')}"
                if c.get('personality'):
                    desc += f"，性格：{'、'.join(c['personality'][:3])}"
                char_desc.append(desc)
            user_parts.append(f"\n已有角色：\n" + "\n".join(char_desc))

        user_prompt = "\n".join(user_parts)

        content = await self._call_claude(system_prompt, user_prompt, temperature=0.8, max_tokens=1000)

        try:
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]
            return json.loads(content.strip())
        except json.JSONDecodeError:
            return {
                "central_conflict": "两人因误会而分离，需要跨越重重障碍才能重逢",
                "plot_hints": ["初次相遇产生误会", "在意外中逐渐了解", "关键时刻做出选择"],
                "suggested_mood": mood or "甜",
                "suggested_ending": "HE",
            }

    async def regenerate_choices(
        self,
        current_content: str,
        characters: list[dict],
        mood: str = "甜",
        hint: str | None = None,
        debug: bool = False,
    ) -> list[dict] | dict:
        """重新生成分支选项"""
        system_prompt = f"""你是一个专业的言情小说作家。
请根据当前的故事内容，生成2-3个不同方向的剧情分支选项。

输出格式（严格 JSON）：
{{
  "choices": [
    {{"label": "A. 选项描述"}},
    {{"label": "B. 选项描述"}},
    {{"label": "C. 选项描述"}}
  ]
}}

只输出 JSON，不要有其他内容。"""

        char_desc = []
        for char in characters[:3]:
            desc = f"{char.get('name', '未命名')}"
            if char.get('personality'):
                desc += f"（{'、'.join(char['personality'][:2])}）"
            char_desc.append(desc)

        user_prompt = f"""当前故事内容：
{current_content[-500:]}

角色：{', '.join(char_desc)}
"""
        if hint:
            user_prompt += f"\n用户希望的方向：{hint}\n"

        raw_response = await self._call_claude(
            system_prompt,
            user_prompt,
            temperature=0.9,
            max_tokens=500,
            return_raw=debug,
        )
        if debug:
            content, response_json = raw_response  # type: ignore[misc]
        else:
            content = raw_response  # type: ignore[assignment]
            response_json = None

        debug_info = None
        if debug:
            debug_info = build_debug_info(
                request_info={
                    "provider": "claude",
                    "model": self.model,
                    "temperature": 0.9,
                    "max_tokens": 500,
                    "system_prompt": system_prompt,
                    "user_prompt": user_prompt,
                },
                response_text=content,
                response_json=response_json,
            )

        try:
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]
            parsed = json.loads(content.strip())
            choices = parsed.get("choices", [])
            choice_items = [{"label": c["label"], "next_node": None} for c in choices]
            if debug:
                return {"choices": choice_items, "debug_info": debug_info}
            return choice_items
        except json.JSONDecodeError:
            fallback = [
                {"label": "A. 继续发展", "next_node": None},
                {"label": "B. 出现转折", "next_node": None},
            ]
            if debug:
                return {"choices": fallback, "debug_info": debug_info}
            return fallback

    async def polish_chapters(
        self,
        chapters: list[dict],
        characters: list[dict],
        writing_style: str = "细腻",
        instruction: str | None = None,
        debug: bool = False,
    ) -> str | dict:
        """整体润色多个章节，使其更通顺连贯"""
        style_prompts = {
            "细腻": "文字风格要细腻，注重心理描写和情感刻画。",
            "白描": "文字风格要简洁白描，用简练的语言勾勒场景和人物。",
            "诗意": "文字风格要诗意唯美，运用优美的比喻和意象。",
            "轻松": "文字风格要轻松活泼，语言俏皮有趣。",
        }

        instruction_text = ""
        if instruction:
            instruction_text = f"\n6. 用户特别要求：{instruction}"

        system_prompt = f"""你是一个专业的言情小说编辑，擅长润色和修改文章。
用户会给你若干段故事内容，请将它们整体润色，使文字更加通顺连贯。

润色要求：
1. 保持原有剧情和对话不变
2. 优化段落之间的衔接
3. 统一文风和用词习惯
4. 调整节奏，删减冗余
5. {style_prompts.get(writing_style, style_prompts['细腻'])}{instruction_text}

只输出润色后的内容，用 "---" 分隔各章节。"""

        chapters_text = []
        for i, chapter in enumerate(chapters):
            title = chapter.get('title', f'第{i+1}章')
            content = chapter.get('content', '')
            chapters_text.append(f"【{title}】\n{content}")

        user_prompt = f"""以下是需要润色的章节内容：

{chr(10).join(chapters_text)}"""

        raw_response = await self._call_claude(
            system_prompt,
            user_prompt,
            temperature=0.7,
            max_tokens=4000,
            return_raw=debug,
        )
        if debug:
            content, response_json = raw_response  # type: ignore[misc]
            return {
                "polished_text": content,
                "debug_info": build_debug_info(
                    request_info={
                        "provider": "claude",
                        "model": self.model,
                        "temperature": 0.7,
                        "max_tokens": 4000,
                        "system_prompt": system_prompt,
                        "user_prompt": user_prompt,
                    },
                    response_text=content,
                    response_json=response_json,
                ),
            }
        return raw_response

    async def generate_outline_draft(
        self,
        story_goal: str,
        character_tension: str,
        central_conflict: str,
        characters: list[dict] | None = None,
        mood: str | None = None,
    ) -> dict:
        """基于引导流程生成大纲草稿"""
        system_prompt = """你是一个专业的言情小说策划师。
根据用户提供的故事目标、角色张力和核心冲突，生成一个完整的故事大纲草稿。

输出必须是严格的 JSON 格式：
{
  "title": "故事标题建议",
  "mood": "情绪基调（甜/虐/搞笑/治愈）",
  "writing_style": "文字风格建议（细腻/白描/诗意/轻松）",
  "central_conflict": "核心冲突（整理后）",
  "ending_type": "结局类型（HE/BE/OE）",
  "plot_hints": ["剧情提示1", "剧情提示2", "剧情提示3", "剧情提示4"],
  "forbidden_elements": ["禁止元素1", "禁止元素2"]
}

只输出 JSON，不要有其他内容。"""

        user_prompt = f"""## 故事目标与读者感受
{story_goal}

## 角色关系与核心张力
{character_tension}

## 剧情核心冲突
{central_conflict}
"""

        if characters:
            char_desc = []
            for c in characters:
                desc = f"- {c.get('name', '未命名')}"
                if c.get('personality'):
                    desc += f"，性格：{'、'.join(c['personality'][:2])}"
                char_desc.append(desc)
            user_prompt += f"\n## 已有角色\n" + "\n".join(char_desc)

        if mood:
            user_prompt += f"\n\n用户期望的情绪基调：{mood}"

        content = await self._call_claude(system_prompt, user_prompt, temperature=0.8, max_tokens=1500)

        try:
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]
            return json.loads(content.strip())
        except json.JSONDecodeError:
            return {
                "title": "未命名故事",
                "mood": mood or "甜",
                "writing_style": "细腻",
                "central_conflict": central_conflict,
                "ending_type": "HE",
                "plot_hints": ["初次相遇", "逐渐了解", "产生情愫", "克服障碍"],
                "forbidden_elements": [],
            }

    async def extract_story_context(
        self,
        content: str,
        debug: bool = False,
    ) -> dict:
        """从当前文本提炼角色/场景/剧情要点"""
        system_prompt = """你是小说策划编辑。请从用户给出的小说正文中提炼结构化信息。

输出必须是严格 JSON：
{
  "characters": [
    {"name":"角色名","role_hint":"主角/配角/反派/未知","traits":["特征1","特征2"],"summary":"不超过40字"}
  ],
  "scenes": [
    {"name":"场景名","description":"不超过40字","keywords":["关键词1","关键词2"]}
  ],
  "outline_points": [
    {"stage":"开端/发展/转折/高潮/结局","summary":"该阶段发生了什么，不超过50字"}
  ],
  "consistency_notes": ["可选：视角/设定矛盾提醒"]
}

规则：
1. 只提炼文本里真实出现或可明确推断的信息，不要杜撰。
2. 角色、场景最多各 8 个，按重要程度排序。
3. 如果某类信息不足，返回空数组。
4. 只输出 JSON，不要额外解释。"""

        clipped_content = (content or "").strip()
        if len(clipped_content) > 12000:
            clipped_content = clipped_content[:12000]

        user_prompt = f"请提炼以下正文：\n\n{clipped_content}"
        raw_response = await self._call_claude(
            system_prompt,
            user_prompt,
            temperature=0.3,
            max_tokens=1800,
            return_raw=debug,
        )

        if debug:
            raw_text, response_json = raw_response  # type: ignore[misc]
        else:
            raw_text = raw_response  # type: ignore[assignment]
            response_json = None

        parsed = self._extract_json_payload(raw_text)
        normalized = self._normalize_extracted_context(parsed)

        if debug:
            normalized["debug_info"] = build_debug_info(
                request_info={
                    "provider": "claude",
                    "model": self.model,
                    "temperature": 0.3,
                    "max_tokens": 1800,
                    "system_prompt": system_prompt,
                    "user_prompt": user_prompt,
                },
                response_text=raw_text,
                response_json=response_json,
            )

        return normalized

    def _extract_json_payload(self, content: str) -> dict:
        text = (content or "").strip()
        if not text:
            return {}

        fenced_match = re.search(r"```(?:json)?\s*(\{[\s\S]*\})\s*```", text)
        if fenced_match:
            text = fenced_match.group(1).strip()

        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            pass

        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            snippet = text[start:end + 1]
            try:
                parsed = json.loads(snippet)
                return parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                return {}
        return {}

    def _normalize_extracted_context(self, data: dict) -> dict:
        characters: list[dict] = []
        scenes: list[dict] = []
        outline_points: list[dict] = []
        notes: list[str] = []

        for item in data.get("characters", []):
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            if not name:
                continue
            traits_raw = item.get("traits", [])
            traits = [str(t).strip() for t in traits_raw if str(t).strip()] if isinstance(traits_raw, list) else []
            characters.append({
                "name": name,
                "role_hint": str(item.get("role_hint", "")).strip() or None,
                "traits": traits[:6],
                "summary": str(item.get("summary", "")).strip() or None,
            })

        for item in data.get("scenes", []):
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            if not name:
                continue
            keywords_raw = item.get("keywords", [])
            keywords = [str(k).strip() for k in keywords_raw if str(k).strip()] if isinstance(keywords_raw, list) else []
            scenes.append({
                "name": name,
                "description": str(item.get("description", "")).strip() or None,
                "keywords": keywords[:6],
            })

        for item in data.get("outline_points", []):
            if not isinstance(item, dict):
                continue
            stage = str(item.get("stage", "")).strip()
            summary = str(item.get("summary", "")).strip()
            if not stage or not summary:
                continue
            outline_points.append({
                "stage": stage,
                "summary": summary,
            })

        notes_raw = data.get("consistency_notes", [])
        if isinstance(notes_raw, list):
            notes = [str(n).strip() for n in notes_raw if str(n).strip()]

        return {
            "extracted_characters": characters[:8],
            "extracted_scenes": scenes[:8],
            "extracted_outline_points": outline_points[:8],
            "extraction_notes": notes[:8],
        }
