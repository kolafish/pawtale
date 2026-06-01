import httpx
from app.config import get_settings
from app.services.ai_base import AITextServiceBase

settings = get_settings()


class DeepSeekTextService(AITextServiceBase):
    """DeepSeek 文本生成服务"""

    def __init__(self):
        self.api_key = settings.deepseek_api_key
        self.base_url = settings.deepseek_base_url

    @property
    def provider_name(self) -> str:
        return "deepseek"

    @property
    def model_name(self) -> str:
        return "deepseek-chat"

    async def _call_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 4000,
        timeout: float = 60.0,
        return_raw: bool = False,
    ) -> str | tuple[str, dict]:
        """调用 DeepSeek API（原生 OpenAI 兼容格式，支持 system role）"""
        messages = []
        if system_prompt.strip():
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_prompt})

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "deepseek-chat",
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
                timeout=timeout,
            )
            response.raise_for_status()
            result = response.json()

        content = result["choices"][0]["message"]["content"]

        if return_raw:
            return content, result
        return content


# 默认文本服务实例
AITextService = DeepSeekTextService
ai_text_service = DeepSeekTextService()
