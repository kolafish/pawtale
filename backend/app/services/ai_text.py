import httpx
from app.config import get_settings
from app.services.ai_base import AITextServiceBase
from app.services.provider_limits import provider_concurrency_limiter

settings = get_settings()


class DeepSeekTextService(AITextServiceBase):
    """DeepSeek 文本生成服务"""

    def __init__(self, model: str | None = None):
        self.api_key = settings.deepseek_api_key
        self.base_url = settings.deepseek_base_url
        self.model = model or settings.deepseek_model
        self.thinking_type = settings.deepseek_thinking_type.strip()
        self.max_concurrent_requests = settings.ai_text_max_concurrent_requests

    @property
    def provider_name(self) -> str:
        return "deepseek"

    @property
    def model_name(self) -> str:
        return self.model

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

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if self.thinking_type:
            payload["thinking"] = {"type": self.thinking_type}

        async def request() -> dict:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.base_url}/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=timeout,
                )
                response.raise_for_status()
                return response.json()

        result = await provider_concurrency_limiter.run(
            "ai_text",
            self.max_concurrent_requests,
            request,
        )

        content = result["choices"][0]["message"]["content"]

        if return_raw:
            return content, result
        return content


# 默认文本服务实例
AITextService = DeepSeekTextService
ai_text_service = DeepSeekTextService()
