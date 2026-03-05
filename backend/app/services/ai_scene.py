import httpx
import json
from app.config import get_settings

settings = get_settings()


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
    ) -> str:
        if not self.deepseek_api_key:
            raise ValueError("DeepSeek API key 未配置")

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
                    "temperature": 0.8,
                    "max_tokens": 3000,
                },
                timeout=60.0,
            )
            response.raise_for_status()
            result = response.json()
            return result["choices"][0]["message"]["content"]

    async def _call_claude(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        if not self.anthropic_auth_token:
            raise ValueError("Anthropic auth token 未配置")

        claude_model = self.claude_model_map.get(model)
        if not claude_model:
            raise ValueError(f"不支持的 Claude 模型: {model}")

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
                    "max_tokens": 2000,
                    "temperature": 0.8,
                    "system": system_prompt,
                    "messages": [
                        {"role": "user", "content": user_prompt},
                    ],
                },
                timeout=90.0,
            )
            response.raise_for_status()
            result = response.json()
            return result["content"][0]["text"]

    async def generate_scene_candidates(
        self,
        category: str,
        mood_keywords: list[str] | None = None,
        character_context: dict | None = None,
        custom_prompt: str | None = None,
        count: int = 3,
        ai_model: str | None = None,
    ) -> list[dict]:
        """生成场景候选"""

        system_prompt = """你是一个专业的言情小说场景设计师，擅长创建富有戏剧张力和浪漫氛围的场景。
请根据用户提供的信息，生成多个场景候选。

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

输出必须是严格的 JSON 数组格式，包含指定数量的场景对象。
只输出 JSON，不要有其他内容。"""

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

        user_parts.append(f"请生成 {count} 个不同的场景候选。")

        user_prompt = "\n".join(user_parts)
        selected_model = (ai_model or "deepseek").lower()
        if selected_model in self.claude_model_map:
            content = await self._call_claude(
                model=selected_model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )
        else:
            content = await self._call_deepseek(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )

        # 解析 JSON
        try:
            # 尝试提取 JSON 部分
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]

            candidates = json.loads(content.strip())

            # 确保返回的是列表
            if not isinstance(candidates, list):
                candidates = [candidates]

            # 规范化每个候选
            normalized = []
            for c in candidates:
                normalized.append({
                    "name": c.get("name", "未命名场景"),
                    "description": c.get("description", ""),
                    "location": c.get("location"),
                    "time_weather": c.get("time_weather"),
                    "mood_keywords": c.get("mood_keywords", []),
                    "dramatic_potential": c.get("dramatic_potential"),
                    "sensory_details": c.get("sensory_details", {}),
                    "props": c.get("props", []),
                })

            return normalized

        except json.JSONDecodeError:
            # 如果解析失败，返回默认场景
            return [
                {
                    "name": f"{category}场景",
                    "description": f"一个适合{category}的浪漫场景",
                    "location": "城市某处",
                    "time_weather": "某个时刻",
                    "mood_keywords": mood_keywords or ["浪漫", "心动"],
                    "dramatic_potential": f"适合{category}类型的剧情发展",
                    "sensory_details": {},
                    "props": [],
                }
            ]


ai_scene_service = AISceneService()
