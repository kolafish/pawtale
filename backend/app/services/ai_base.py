import json
import logging
import re
from abc import ABC, abstractmethod
from app.services.ai_debug import build_debug_info

logger = logging.getLogger(__name__)


class AITextServiceBase(ABC):
    """AI 文本生成服务基类

    子类只需实现 _call_llm() 和两个属性 provider_name / model_name。
    所有 prompt 构建、响应解析、debug 包装均在基类完成。
    """

    # ── 子类必须实现 ──────────────────────────

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """返回 'claude' 或 'deepseek'，用于 debug_info"""
        ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        """返回模型标识符，如 'claude-opus-4-6' 或 'deepseek-chat'"""
        ...

    @abstractmethod
    async def _call_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 4000,
        timeout: float = 60.0,
        return_raw: bool = False,
    ) -> str | tuple[str, dict]:
        """调用 LLM，子类实现具体的 HTTP 传输。

        return_raw=True 时返回 (content_text, raw_response_json)。
        """
        ...

    # ── 共享辅助方法 ──────────────────────────

    def _wrap_debug(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
        content: str,
        response_json: dict | None,
    ) -> dict:
        return build_debug_info(
            request_info={
                "provider": self.provider_name,
                "model": self.model_name,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
            },
            response_text=content,
            response_json=response_json,
        )

    @staticmethod
    def _extract_json(content: str) -> str:
        """从可能包含 ```json 代码块的文本中提取 JSON 字符串"""
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]
        return content.strip()

    @staticmethod
    def _strip_json_trailing_commas(text: str) -> str:
        """Remove trailing commas before } or ] (common LLM JSON error)."""
        return re.sub(r",\s*([}\]])", r"\1", text)

    @classmethod
    def _try_parse_json(cls, text: str) -> dict | None:
        """Try json.loads, with fallback for trailing commas."""
        for candidate in (text, cls._strip_json_trailing_commas(text)):
            try:
                parsed = json.loads(candidate)
                return parsed if isinstance(parsed, dict) else None
            except (json.JSONDecodeError, ValueError):
                continue
        return None

    @classmethod
    def _extract_json_payload(cls, content: str) -> dict:
        """更健壮的 JSON 提取（支持 fenced block、裸 JSON、首尾匹配、trailing commas）"""
        text = (content or "").strip()
        if not text:
            return {}

        # Try closed fenced block first
        fenced_match = re.search(r"```(?:json)?\s*(\{[\s\S]*\})\s*```", text)
        if fenced_match:
            text = fenced_match.group(1).strip()
        else:
            # Also handle unclosed fenced block (truncated response)
            open_match = re.search(r"```(?:json)?\s*(\{[\s\S]*)", text)
            if open_match:
                text = open_match.group(1).strip()

        parsed = cls._try_parse_json(text)
        if parsed is not None:
            return parsed

        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            snippet = text[start:end + 1]
            parsed = cls._try_parse_json(snippet)
            if parsed is not None:
                return parsed
        return {}

    @staticmethod
    def _build_char_descriptions(characters: list[dict]) -> list[str]:
        """构建角色描述列表（完整版，用于 generate_story 等）"""
        descs = []
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
            descs.append(desc)
        return descs

    @staticmethod
    def _build_scene_description(scene: dict) -> str:
        """构建场景描述"""
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
        return scene_desc

    @staticmethod
    def _get_story_stage(chapter_number: int) -> dict:
        """根据章节数判断故事阶段"""
        if chapter_number == 1:
            return {
                "name": "开篇",
                "guidance": "重点是建立人物形象和初始关系，制造第一个吸引点。节奏可以稍快，但要留下悬念。"
            }
        elif chapter_number <= 3:
            return {
                "name": "铺垫期",
                "guidance": "逐步展开人物性格，制造小冲突或暧昧时刻。可以埋下伏笔，但不要急于推进主线。"
            }
        elif chapter_number <= 6:
            return {
                "name": "发展期",
                "guidance": "关系开始变化，可以有误会、靠近、或小高潮。情感浓度逐渐上升。"
            }
        elif chapter_number <= 9:
            return {
                "name": "高潮期",
                "guidance": "核心冲突爆发或关系重大转折。情感张力要拉满，可以有强烈的情绪波动。"
            }
        else:
            return {
                "name": "收尾期",
                "guidance": "回收伏笔，解决冲突，给读者情感满足。节奏可以放缓，注重情感沉淀。"
            }

    @staticmethod
    def _build_style_guide(writing_style: str, mood: str, story_stage: dict) -> str:
        """构建具体的风格指导"""
        style_rules = {
            "细腻": """
- 注重内心戏：角色的每个重要动作后，要有内心反应
- 善用微表情：眼神闪烁、嘴角弧度、手指动作等细节
- 情感层次：同一场景中情绪要有起伏变化，不是一条直线
- 句式：长短句交替，紧张时短句，抒情时长句
- 比喻要贴合角色视角，不要用作者视角的华丽比喻""",
            "白描": """
- 克制修饰：形容词控制在必要范围，让动作和对话说话
- 留白艺术：不说破，让读者自己体会
- 精准动词：用一个准确的动词代替"形容词+普通动词"
- 对话为主：通过对话推进，减少大段描写
- 冷静叙述：即使是激烈场面也保持叙述的冷静""",
            "诗意": """
- 意象营造：善用自然意象（雨、风、光影）映射情感
- 节奏韵律：注意句子的音韵美，可以有排比和对仗
- 通感手法：打通视觉、听觉、触觉的界限
- 氛围优先：氛围营造比情节推进更重要
- 留有余韵：章节结尾要有诗意的收束""",
            "轻松": """
- 语言俏皮：可以用网络流行语，但不要过时的梗
- 吐槽视角：角色可以有内心吐槽，增加喜感
- 节奏明快：不要长段抒情，保持轻快节奏
- 反差萌：严肃场合的小意外，高冷人设的可爱瞬间
- 对话为王：用有趣的对话推进，减少描写性文字""",
        }
        mood_additions = {
            "甜": "\n- 甜度把控：心动瞬间要有铺垫，不能突然发糖\n- 小细节制造心动：不经意的触碰、对视、为对方做的小事",
            "虐": "\n- 虐要有因：误会和冲突要有合理原因，不能为虐而虐\n- 虐中带希望：即使虐心，也要让读者看到感情的真实",
            "搞笑": "\n- 笑点自然：搞笑要从情境和人物性格中来，不要硬凹\n- 反差制造笑点：期待与现实的落差是最好的喜剧",
            "治愈": "\n- 温暖不腻：治愈感来自真实的善意，不是刻意的温情\n- 生活细节：用日常小事传递温暖，不要大道理",
        }
        return style_rules.get(writing_style, style_rules["细腻"]) + mood_additions.get(mood, "")

    def _parse_story_response(self, content: str) -> dict:
        """解析 AI 返回的故事内容和分支选项"""
        chapter_title = None
        choices: list[dict] = []

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
            for line in choices_part.strip().split("\n"):
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

    @staticmethod
    def _normalize_extracted_context(data: dict) -> dict:
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
            outline_points.append({"stage": stage, "summary": summary})

        notes_raw = data.get("consistency_notes", [])
        if isinstance(notes_raw, list):
            notes = [str(n).strip() for n in notes_raw if str(n).strip()]

        return {
            "extracted_characters": characters[:8],
            "extracted_scenes": scenes[:8],
            "extracted_outline_points": outline_points[:8],
            "extraction_notes": notes[:8],
        }

    # ── 公开 API 方法（模板方法模式） ─────────────

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
        content = await self._call_llm(system_prompt, user_prompt, temperature=0.7, max_tokens=1000, timeout=30.0)

        try:
            return json.loads(self._extract_json(content))
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
        char_descriptions = self._build_char_descriptions(characters)
        scene_desc = self._build_scene_description(scene)
        story_stage = self._get_story_stage(chapter_number)
        style_guide = self._build_style_guide(writing_style, mood, story_stage)

        length_config = {
            "短": {"words": "300-400", "desc": "简洁精炼，聚焦核心情节"},
            "中": {"words": "500-700", "desc": "详略得当，情节与情感并重"},
            "长": {"words": "900-1200", "desc": "充分展开，细腻描写"},
        }
        length_info = length_config.get(chapter_length, length_config["中"])

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

### 章节衔接要求
- 开头要有"呼吸感"：可以是环境描写、角色内心、或时间流逝的暗示
- 与上一章的情感要有承接，不能情绪断层
- 如果上章结尾是紧张/尴尬，本章开头要延续那种氛围再逐渐转变

### 对话写作要求
- 对话后要有反应：表情、动作、内心活动，不能直接接下一句对话
- 角色说话要符合其性格和说话风格
- 适当使用省略号、破折号表现真实的说话节奏
- 每个角色的语言习惯要有区分度

### 细节与闲笔
- 每章至少有一处"闲笔"：与主线无关但能体现角色性格的小细节
- 善用环境细节映射人物心理
- 适当加入感官描写（不只是视觉）

## 输出格式
【章节标题】
（4-8字，有意境，能概括本章情感或转折）

【故事内容】
（正文）

【分支选项】
A. 选项描述（10-20字，暗示不同走向）
B. 选项描述
C. 选项描述（可选）

分支选项要求：
- 每个选项代表明显不同的故事走向
- 选项本身要有吸引力，让读者想点
- 可以是行动选择、态度选择、或意外事件"""

        user_prompt = f"""## 角色设定
{chr(10).join(char_descriptions)}

## 场景
{scene_desc}

## 情绪基调：{mood}
"""
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

        if story_state:
            user_prompt += "\n## 当前故事状态\n"
            if story_state.get('relationship_stage'):
                user_prompt += f"关系阶段：{story_state['relationship_stage']}\n"
            if story_state.get('recent_events'):
                user_prompt += f"近期事件：{story_state['recent_events']}\n"
            if story_state.get('emotional_tone'):
                user_prompt += f"当前情感基调：{story_state['emotional_tone']}\n"
            if story_state.get('pending_threads'):
                user_prompt += f"待回收伏笔：{'、'.join(story_state['pending_threads'])}\n"

        if previous_content:
            user_prompt += f"\n## 前情提要\n{previous_content}\n"
            user_prompt += "\n## 续写要求\n请自然承接上文，注意：\n"
            user_prompt += "1. 不要重复上章已有的内容\n"
            user_prompt += "2. 开头要有过渡，不能直接接动作\n"
            user_prompt += "3. 延续上章结尾的情感氛围\n"
            if custom_direction:
                user_prompt += f"\n用户希望的发展方向：{custom_direction}\n"
        else:
            user_prompt += "\n## 开篇要求\n这是故事的开篇，需要：\n"
            user_prompt += "1. 快速建立场景氛围\n"
            user_prompt += "2. 自然引出主要角色\n"
            user_prompt += "3. 在结尾制造一个小钩子，吸引读者继续\n"
            if custom_direction:
                user_prompt += f"\n用户希望的开篇方向：{custom_direction}\n"

        temp = 0.85
        raw_response = await self._call_llm(system_prompt, user_prompt, temperature=temp, max_tokens=3000, timeout=90.0, return_raw=debug)
        if debug:
            content, response_json = raw_response  # type: ignore[misc]
        else:
            content = raw_response  # type: ignore[assignment]
            response_json = None

        parsed_result = self._parse_story_response(content)
        if debug:
            parsed_result["debug_info"] = self._wrap_debug(system_prompt, user_prompt, temp, 3000, content, response_json)
        return parsed_result

    async def generate_chapter_title(self, content: str) -> str:
        """为已有内容生成章节标题"""
        system_prompt = """你是一个专业的言情小说编辑。
请根据给定的故事内容，生成一个有意境的章节标题。
标题要求：4-8个字，能概括本章主要内容或情感，有文学性。
只输出标题，不要有其他内容。"""

        result = await self._call_llm(system_prompt, f"故事内容：\n{content[:500]}", temperature=0.7, max_tokens=50, timeout=15.0)
        return result.strip()

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
            "细腻": "文字风格要细腻，注重心理描写和情感刻画",
            "白描": "文字风格要简洁白描，用简练的语言勾勒场景和人物",
            "诗意": "文字风格要诗意唯美，运用优美的比喻和意象",
            "轻松": "文字风格要轻松活泼，语言俏皮有趣",
        }
        length_prompts = {
            "短": "修改后控制在200-300字左右",
            "中": "修改后控制在400-600字左右",
            "长": "修改后控制在800-1200字左右",
        }

        char_section = ""
        if characters:
            char_descs = []
            for char in characters:
                desc = f"【{char['name']}】"
                if char.get('identity'):
                    desc += f" {char['identity']}"
                if char.get('personality'):
                    desc += f"，性格：{'、'.join(char['personality'])}"
                if char.get('speaking_style'):
                    desc += f"，说话风格：{char['speaking_style']}"
                char_descs.append(desc)
            char_section = f"\n角色设定：\n{chr(10).join(char_descs)}\n"

        system_prompt = "你是一位专业的文字编辑。"
        user_prompt = f"""请根据修改意见，对以下文本内容进行修改和重写。

要求：
- {style_prompts.get(writing_style, style_prompts['细腻'])}
- {length_prompts.get(chapter_length, length_prompts['中'])}
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
        raw_response = await self._call_llm(system_prompt, user_prompt, temperature=temp, max_tokens=3000, timeout=90.0, return_raw=debug)
        if debug:
            content, response_json = raw_response  # type: ignore[misc]
        else:
            content = raw_response  # type: ignore[assignment]
            response_json = None

        result = {"content": content.strip()}
        if debug:
            result["debug_info"] = self._wrap_debug(system_prompt, user_prompt, temp, 3000, content, response_json)
        return result

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
  "central_conflict": "核心冲突描述（50-100字，描述主角之间的主要矛盾或障碍）",
  "plot_hints": ["剧情提示1", "剧情提示2", "剧情提示3"],
  "suggested_mood": "建议的情绪基调（甜/虐/搞笑/治愈）",
  "suggested_ending": "建议的结局类型（HE/BE/OE）"
}

剧情提示要求：
1. 每条提示是一个可能的剧情转折点或关键事件
2. 提示之间要有递进关系，从相识到发展到高潮
3. 要与核心冲突呼应
4. 3-5条提示为宜

核心冲突设计原则：
1. 要有足够的戏剧张力
2. 要与角色性格和背景相关
3. 要有解决的可能性（除非是 BE 结局）

只输出 JSON，不要有其他内容。"""

        user_parts = [f"故事标题/主题：{title}"]
        if mood:
            user_parts.append(f"期望的情绪基调：{mood}")
        if characters:
            char_desc = []
            for c in characters:
                desc = f"- {c.get('name', '未命名')}"
                if c.get('role_type'):
                    role_map = {'protagonist': '主角', 'deuteragonist': '第二主角', 'supporting': '配角', 'antagonist': '反派'}
                    desc += f"（{role_map.get(c['role_type'], c['role_type'])}）"
                if c.get('personality'):
                    desc += f"，性格：{'、'.join(c['personality'][:3])}"
                if c.get('identity'):
                    desc += f"，身份：{c['identity']}"
                if c.get('core_trait'):
                    desc += f"，核心特质：{c['core_trait']}"
                char_desc.append(desc)
            user_parts.append(f"\n已有角色：\n" + "\n".join(char_desc))
        if scenes:
            scene_desc = []
            for s in scenes:
                desc = f"- {s.get('name', '未命名')}"
                if s.get('category'):
                    desc += f"（{s['category']}）"
                scene_desc.append(desc)
            user_parts.append(f"\n已有场景：\n" + "\n".join(scene_desc))

        user_prompt = "\n".join(user_parts)
        content = await self._call_llm(system_prompt, user_prompt, temperature=0.8, max_tokens=1000, timeout=30.0)

        try:
            return json.loads(self._extract_json(content))
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

要求：
1. 每个选项要有明显不同的走向
2. 选项要符合当前的情绪基调（{mood}）
3. 选项要与角色性格相符
4. 选项描述要简洁有吸引力（10-20字）

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
            user_prompt += f"\n用户希望的方向：{hint}\n请根据这个方向生成选项。"

        temp = 0.9
        raw_response = await self._call_llm(system_prompt, user_prompt, temperature=temp, max_tokens=500, timeout=20.0, return_raw=debug)
        if debug:
            content, response_json = raw_response  # type: ignore[misc]
        else:
            content = raw_response  # type: ignore[assignment]
            response_json = None

        debug_info = None
        if debug:
            debug_info = self._wrap_debug(system_prompt, user_prompt, temp, 500, content, response_json)

        try:
            parsed = json.loads(self._extract_json(content))
            choices = parsed.get("choices", [])
            choice_items = [{"label": c["label"], "next_node": None} for c in choices]
            if debug:
                return {"choices": choice_items, "debug_info": debug_info}
            return choice_items
        except json.JSONDecodeError:
            fallback = [
                {"label": "A. 继续发展", "next_node": None},
                {"label": "B. 出现转折", "next_node": None},
                {"label": "C. 意外惊喜", "next_node": None},
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
2. 优化段落之间的衔接，消除重复描述
3. 统一文风和用词习惯
4. 调整节奏，删减冗余，强化情感高潮
5. {style_prompts.get(writing_style, style_prompts['细腻'])}{instruction_text}

注意：
- 不要改变故事走向和人物性格
- 不要添加新的剧情
- 保留原有的对话内容
- 输出润色后的完整内容，用 "---" 分隔各章节

只输出润色后的内容，不要有其他说明。"""

        char_desc = []
        for char in characters[:4]:
            desc = f"{char.get('name', '未命名')}"
            if char.get('personality'):
                desc += f"（{'、'.join(char['personality'][:2])}）"
            char_desc.append(desc)

        chapters_text = []
        for i, chapter in enumerate(chapters):
            t = chapter.get('title', f'第{i+1}章')
            c = chapter.get('content', '')
            chapters_text.append(f"【{t}】\n{c}")

        user_prompt = f"""角色：{', '.join(char_desc)}

以下是需要润色的章节内容：

{chr(10).join(chapters_text)}

请整体润色以上内容，使其更加通顺连贯。用 "---" 分隔各章节。"""

        temp = 0.7
        raw_response = await self._call_llm(system_prompt, user_prompt, temperature=temp, max_tokens=4000, timeout=90.0, return_raw=debug)
        if debug:
            content, response_json = raw_response  # type: ignore[misc]
            return {
                "polished_text": content,
                "debug_info": self._wrap_debug(system_prompt, user_prompt, temp, 4000, content, response_json),
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

        content = await self._call_llm(system_prompt, user_prompt, temperature=0.8, max_tokens=1500, timeout=30.0)

        try:
            return json.loads(self._extract_json(content))
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
        """从已写文本中提炼角色/场景/剧情要点"""
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
        temp = 0.3
        raw_response = await self._call_llm(system_prompt, user_prompt, temperature=temp, max_tokens=1800, timeout=60.0, return_raw=debug)

        if debug:
            raw_text, response_json = raw_response  # type: ignore[misc]
        else:
            raw_text = raw_response  # type: ignore[assignment]
            response_json = None

        parsed = self._extract_json_payload(raw_text)
        normalized = self._normalize_extracted_context(parsed)

        if debug:
            normalized["debug_info"] = self._wrap_debug(system_prompt, user_prompt, temp, 1800, raw_text, response_json)
        return normalized

    async def analyze_project_content(
        self,
        content: str,
        debug: bool = False,
    ) -> dict:
        """从项目手稿中深度提取人物、大纲、设定信息"""
        # Keep system prompt minimal — it gets merged into user message for Claude proxy,
        # and the proxy has input size limits. Put the JSON schema in user_prompt instead.
        system_prompt = "你是专业的小说策划编辑。只输出JSON，不要任何额外文字。"

        clipped = (content or "").strip()
        if len(clipped) > 12000:
            clipped = clipped[:12000]

        user_prompt = f"""从以下小说正文中提取人物、大纲、设定，输出严格JSON：
{{"characters":[{{"name":"角色名","gender":"男/女/null","identity":"身份","personality":["性格"],"appearance":"外貌","relationships":"关系","summary":"概述100字内"}}],"outlines":[{{"name":"大纲名","content":"梗概300字内"}}],"settings":[{{"name":"设定名","type":"location或note","content":"描述"}}]}}

规则：只提炼文本中真实出现的信息。characters最多10个。outlines至少1个总结已有剧情。settings最多8个。

正文：
{clipped}"""
        temp = 0.3

        # Retry up to 3 times — proxy/network may drop connections on large requests
        import asyncio as _asyncio
        max_attempts = 3
        raw_text = ""
        response_json = None
        parsed: dict = {}
        last_error: Exception | None = None
        for attempt in range(max_attempts):
            try:
                raw_response = await self._call_llm(
                    system_prompt, user_prompt,
                    temperature=temp, max_tokens=3000, timeout=120.0, return_raw=debug,
                )
            except Exception as e:
                last_error = e
                # Log full details for proxy/network errors
                err_detail = str(e)
                if hasattr(e, 'response') and e.response is not None:
                    err_detail += f" | status={e.response.status_code} body={e.response.text[:500]}"
                logger.warning("analyze_project_content attempt %d/%d [%s/%s]: %s",
                               attempt + 1, max_attempts, self.provider_name, self.model_name, err_detail)
                if attempt < max_attempts - 1:
                    await _asyncio.sleep(3 + attempt * 2)
                continue

            if debug:
                raw_text, response_json = raw_response  # type: ignore[misc]
            else:
                raw_text = raw_response  # type: ignore[assignment]
                response_json = None

            parsed = self._extract_json_payload(raw_text)
            has_data = bool(
                parsed.get("characters") or parsed.get("outlines") or parsed.get("settings")
            )
            if has_data:
                last_error = None
                break
            logger.warning(
                "analyze_project_content attempt %d/%d: JSON parse yielded empty result. "
                "raw_text[:200]=%s",
                attempt + 1, max_attempts,
                (raw_text or "")[:200],
            )
            if attempt < max_attempts - 1:
                await _asyncio.sleep(2)

        if last_error is not None:
            raise last_error

        # Normalize output
        result: dict = {
            "characters": [],
            "outlines": [],
            "settings": [],
        }
        if isinstance(parsed, dict):
            for char in (parsed.get("characters") or [])[:10]:
                if isinstance(char, dict) and char.get("name"):
                    result["characters"].append({
                        "name": str(char["name"]).strip(),
                        "gender": char.get("gender"),
                        "identity": char.get("identity"),
                        "personality": char.get("personality") or [],
                        "appearance": char.get("appearance"),
                        "relationships": char.get("relationships"),
                        "summary": char.get("summary"),
                    })
            for outline in (parsed.get("outlines") or [])[:5]:
                if isinstance(outline, dict) and outline.get("name"):
                    result["outlines"].append({
                        "name": str(outline["name"]).strip(),
                        "content": str(outline.get("content") or "").strip(),
                    })
            for setting in (parsed.get("settings") or [])[:8]:
                if isinstance(setting, dict) and setting.get("name"):
                    s_type = setting.get("type", "note")
                    if s_type not in ("location", "note"):
                        s_type = "note"
                    result["settings"].append({
                        "name": str(setting["name"]).strip(),
                        "type": s_type,
                        "content": str(setting.get("content") or "").strip(),
                    })

        if debug:
            result["debug_info"] = self._wrap_debug(system_prompt, user_prompt, temp, 4096, raw_text, response_json)
        return result
