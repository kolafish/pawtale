import logging
from abc import ABC, abstractmethod
from app.services.ai_debug import build_debug_info
from app.services.json_utils import extract_json_payload, strip_json_trailing_commas, try_parse_json

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
        return strip_json_trailing_commas(text)

    @classmethod
    def _try_parse_json(cls, text: str) -> dict | None:
        """Try json.loads, with fallback for trailing commas."""
        parsed = try_parse_json(text)
        return parsed if isinstance(parsed, dict) else None

    @classmethod
    def _extract_json_payload(cls, content: str) -> dict:
        """更健壮的 JSON 提取（支持 fenced block、裸 JSON、首尾匹配、trailing commas）"""
        parsed = extract_json_payload(content)
        return parsed if isinstance(parsed, dict) else {}

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

    # ── 公开 API 方法（模板方法模式） ─────────────

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
