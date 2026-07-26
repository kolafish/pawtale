import httpx
from app.config import get_settings
from app.services.ai_base import AITextServiceBase
from app.services.ai_timeouts import (
    AIProviderTimeoutError,
    effective_ai_text_timeout_seconds,
)
from app.services.provider_limits import provider_concurrency_limiter

settings = get_settings()

CLAUDE_FABLE_MODEL = "claude-fable-5"
CLAUDE_OPUS_4_8_MODEL = "claude-opus-4-8"


class ClaudeTextService(AITextServiceBase):
    """Claude 文本生成服务（通过 OpenAI 兼容代理）"""

    def __init__(self, model: str = CLAUDE_FABLE_MODEL):
        self.auth_token = settings.anthropic_auth_token
        self.base_url = settings.anthropic_base_url
        model_map = {
            CLAUDE_FABLE_MODEL: CLAUDE_FABLE_MODEL,
            CLAUDE_OPUS_4_8_MODEL: CLAUDE_OPUS_4_8_MODEL,
            "claude-sonnet-4": "claude-sonnet-4-6",
        }
        self.model = model_map.get(model, CLAUDE_FABLE_MODEL)
        self.max_concurrent_requests = settings.ai_text_max_concurrent_requests

    @property
    def provider_name(self) -> str:
        return "claude"

    @property
    def model_name(self) -> str:
        return self.model

    async def _call_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 4000,
        timeout: float | None = None,
        return_raw: bool = False,
    ) -> str | tuple[str, dict]:
        """调用 Claude API（通过 OpenAI 兼容代理）

        代理忽略 system role，所以将 system_prompt 合并到 user message。
        """
        merged = f"{system_prompt}\n\n{user_prompt}" if system_prompt.strip() else user_prompt
        request_timeout = effective_ai_text_timeout_seconds(timeout)

        async def request() -> dict:
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        self._chat_completions_url(),
                        headers={
                            "Authorization": f"Bearer {self.auth_token}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": self.model,
                            "max_tokens": max_tokens,
                            "temperature": temperature,
                            "messages": [
                                {"role": "user", "content": merged},
                            ],
                        },
                        timeout=request_timeout,
                    )
                    response.raise_for_status()
                    return response.json()
            except httpx.TimeoutException as exc:
                raise AIProviderTimeoutError(
                    provider=self.provider_name,
                    model=self.model_name,
                    timeout_seconds=request_timeout,
                ) from exc

        result = await provider_concurrency_limiter.run(
            "ai_text",
            self.max_concurrent_requests,
            request,
        )

        content = self._extract_response_text(result)
        if not content:
            raise ValueError("Claude 返回内容为空或格式无法解析")

        if return_raw:
            return content, result
        return content

    def _chat_completions_url(self) -> str:
        base_url = self.base_url.rstrip("/")
        if base_url.endswith("/v1"):
            return f"{base_url}/chat/completions"
        return f"{base_url}/v1/chat/completions"

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
