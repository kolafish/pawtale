import httpx
import json
from collections.abc import Iterable
from app.config import get_settings
from app.services.ai_debug import build_debug_info

settings = get_settings()


class AISceneError(Exception):
    """场景 AI 生成基础异常"""

    def __init__(self, message: str, debug_info: dict | None = None):
        super().__init__(message)
        self.debug_info = debug_info


class AISceneTimeoutError(AISceneError):
    """上游超时"""


class AISceneProviderError(AISceneError):
    """上游服务异常"""


class AISceneParseError(AISceneError):
    """上游返回无法解析或质量不达标"""


class AISceneService:
    """多模型场景生成服务（DeepSeek / Claude）"""

    def __init__(self):
        self.deepseek_api_key = settings.deepseek_api_key
        self.deepseek_base_url = settings.deepseek_base_url
        self.anthropic_auth_token = settings.anthropic_auth_token
        self.anthropic_base_url = settings.anthropic_base_url
        self.claude_model_map = {
            "claude-opus-4": "claude-opus-4-20250514",
            "claude-sonnet-4": "claude-sonnet-4-20250514",
        }

    async def _call_deepseek(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> tuple[str, dict]:
        if not self.deepseek_api_key:
            raise AISceneProviderError("DeepSeek API key 未配置")

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.deepseek_base_url}/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.deepseek_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "deepseek-chat",
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        "temperature": 0.55,
                        "max_tokens": 1400,
                    },
                    timeout=45.0,
                )
                response.raise_for_status()
                result = response.json()
        except httpx.TimeoutException as exc:
            raise AISceneTimeoutError("DeepSeek 生成超时，请稍后重试") from exc
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code if exc.response else "unknown"
            raise AISceneProviderError(f"DeepSeek 服务异常（HTTP {status_code}）") from exc
        except httpx.HTTPError as exc:
            raise AISceneProviderError("DeepSeek 服务连接失败") from exc

        try:
            return result["choices"][0]["message"]["content"], result
        except (KeyError, IndexError, TypeError) as exc:
            raise AISceneParseError("DeepSeek 返回内容缺失") from exc

    async def _call_claude(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
    ) -> tuple[str, dict]:
        if not self.anthropic_auth_token:
            raise AISceneProviderError("Anthropic auth token 未配置")

        claude_model = self.claude_model_map.get(model)
        if not claude_model:
            raise AISceneProviderError(f"不支持的 Claude 模型: {model}")

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.anthropic_base_url}/v1/messages",
                    headers={
                        "Authorization": f"Bearer {self.anthropic_auth_token}",
                        "Content-Type": "application/json",
                        "anthropic-version": "2023-06-01",
                    },
                    json={
                        "model": claude_model,
                        "max_tokens": 1600,
                        "temperature": 0.6,
                        "system": system_prompt,
                        "messages": [
                            {"role": "user", "content": user_prompt},
                        ],
                    },
                    timeout=65.0,
                )
                response.raise_for_status()
                result = response.json()
        except httpx.TimeoutException as exc:
            raise AISceneTimeoutError("Claude 生成超时，请稍后重试") from exc
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code if exc.response else "unknown"
            raise AISceneProviderError(f"Claude 服务异常（HTTP {status_code}）") from exc
        except httpx.HTTPError as exc:
            raise AISceneProviderError("Claude 服务连接失败") from exc

        content_blocks = result.get("content")
        if not isinstance(content_blocks, list):
            raise AISceneParseError("Claude 返回内容格式异常")

        texts: list[str] = []
        for block in content_blocks:
            if not isinstance(block, dict):
                continue
            text = block.get("text")
            if not isinstance(text, str):
                continue
            block_type = block.get("type")
            if block_type in (None, "text"):
                texts.append(text)

        content = "\n".join(t.strip() for t in texts if t and t.strip()).strip()
        if not content:
            raise AISceneParseError("Claude 返回为空")
        return content, result

    def _unpack_model_response(self, result: str | tuple[str, dict]) -> tuple[str, dict | None]:
        if isinstance(result, tuple) and len(result) == 2:
            return result[0], result[1]
        return result, None

    def _extract_json_payload(self, content: str) -> list | dict:
        decoder = json.JSONDecoder()
        text = content.strip()

        # 1) 先尝试整体 JSON
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 2) 尝试 fenced code block
        for block in self._iter_fenced_blocks(text):
            try:
                return json.loads(block.strip())
            except json.JSONDecodeError:
                continue

        # 3) 从任意噪音文本中查找首个可解析 JSON 片段
        for idx, ch in enumerate(text):
            if ch not in "[{":
                continue
            try:
                parsed, _ = decoder.raw_decode(text[idx:])
                if isinstance(parsed, (list, dict)):
                    return parsed
            except json.JSONDecodeError:
                continue

        raise AISceneParseError("模型返回不是可解析的 JSON")

    def _iter_fenced_blocks(self, text: str) -> Iterable[str]:
        parts = text.split("```")
        for i in range(1, len(parts), 2):
            block = parts[i]
            lowered = block.lower().lstrip()
            if lowered.startswith("json"):
                # 去掉首行 json 标识
                lines = block.splitlines()
                if lines:
                    yield "\n".join(lines[1:])
                else:
                    yield ""
            else:
                yield block

    def _parse_candidates_payload(self, payload: list | dict, count: int) -> list[dict]:
        raw_candidates: list
        if isinstance(payload, list):
            raw_candidates = payload
        elif isinstance(payload, dict):
            if isinstance(payload.get("candidates"), list):
                raw_candidates = payload["candidates"]
            elif isinstance(payload.get("scenes"), list):
                raw_candidates = payload["scenes"]
            else:
                raise AISceneParseError("模型返回 JSON 缺少 candidates/scenes 数组")
        else:
            raise AISceneParseError("模型返回 JSON 类型不支持")

        normalized: list[dict] = []
        for item in raw_candidates:
            if not isinstance(item, dict):
                continue

            name = str(item.get("name", "")).strip()
            description = str(item.get("description", "")).strip()
            if not name or not description:
                continue

            mood_keywords = item.get("mood_keywords", [])
            if isinstance(mood_keywords, str):
                mood_keywords = [
                    k.strip() for k in mood_keywords.replace("，", ",").split(",") if k.strip()
                ]
            if not isinstance(mood_keywords, list):
                mood_keywords = []

            sensory_details = item.get("sensory_details", {})
            if not isinstance(sensory_details, dict):
                sensory_details = {}

            props = item.get("props", [])
            if isinstance(props, str):
                props = [p.strip() for p in props.replace("，", ",").split(",") if p.strip()]
            if not isinstance(props, list):
                props = []

            normalized.append({
                "name": name,
                "description": description,
                "location": item.get("location"),
                "time_weather": item.get("time_weather"),
                "mood_keywords": mood_keywords,
                "dramatic_potential": item.get("dramatic_potential"),
                "sensory_details": sensory_details,
                "props": props,
            })

        if not normalized:
            raise AISceneParseError("模型返回的场景候选无效（缺少 name/description）")

        return normalized[:count]

    async def generate_scene_candidates(
        self,
        category: str,
        mood_keywords: list[str] | None = None,
        character_context: dict | None = None,
        custom_prompt: str | None = None,
        count: int = 3,
        ai_model: str | None = None,
        debug: bool = False,
    ) -> tuple[list[dict], dict | None]:
        """生成场景候选"""

        system_prompt = """你是一个专业的言情小说场景设计师。你必须严格围绕用户给定的场景类型和关键词生成候选场景，不得偏题。
请根据用户提供的信息，生成多个场景候选，保证可直接用于剧情创作。

每个场景需要包含以下信息：
1. name: 场景名称（4-8个字，有意境）
2. description: 场景描述（50-100字，描述场景氛围和可能发生的事）
3. location: 具体地点
4. time_weather: 时间和天气
5. mood_keywords: 氛围关键词（3-5个）
6. dramatic_potential: 戏剧潜力（这个场景适合什么类型的剧情发展）
7. sensory_details: 感官细节
   - visual: 视觉描述
   - auditory: 听觉描述
   - olfactory: 嗅觉描述
8. props: 可能用到的道具或元素（3-5个）

硬性要求：
- 不要输出任何与给定场景类型无关的内容。
- 必须使用用户给定的氛围关键词，不能替换为不相关主题。
- 所有候选场景彼此差异明显，且符合言情叙事语境。
- 输出必须是严格 JSON（数组，或 {"candidates": [...]} / {"scenes":[...]}）。
- 只输出 JSON，不要输出解释文本。"""

        # 构建用户提示
        user_parts = [f"场景类型：{category}"]

        if mood_keywords:
            user_parts.append(f"氛围关键词：{', '.join(mood_keywords)}")

        if character_context:
            char_ids = character_context.get("character_ids", [])
            relationship = character_context.get("relationship", "")
            if relationship:
                user_parts.append(f"角色关系：{relationship}")
            if char_ids:
                user_parts.append(f"涉及 {len(char_ids)} 个角色")

        if custom_prompt:
            user_parts.append(f"用户要求：{custom_prompt}")

        user_parts.append(f"请生成 {count} 个不同的场景候选，且全部紧扣「{category}」。")

        user_prompt = "\n".join(user_parts)
        selected_model = (ai_model or "deepseek").lower()
        provider = "deepseek"
        request_model = "deepseek-chat"
        temperature = 0.55
        max_tokens = 1400
        if selected_model in self.claude_model_map:
            provider = "claude"
            request_model = self.claude_model_map[selected_model]
            temperature = 0.6
            max_tokens = 1600
            model_result = await self._call_claude(
                model=selected_model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )
        else:
            model_result = await self._call_deepseek(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )

        content, raw_json = self._unpack_model_response(model_result)
        debug_info = None
        if debug:
            debug_info = build_debug_info(
                request_info={
                    "provider": provider,
                    "model": request_model,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "system_prompt": system_prompt,
                    "user_prompt": user_prompt,
                },
                response_text=content,
                response_json=raw_json,
            )

        try:
            payload = self._extract_json_payload(content)
            candidates = self._parse_candidates_payload(payload, count)
            return candidates, debug_info
        except AISceneParseError as exc:
            if debug:
                raise AISceneParseError(str(exc), debug_info=debug_info) from exc
            raise


ai_scene_service = AISceneService()
