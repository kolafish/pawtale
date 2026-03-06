import json
import re
from datetime import datetime, timezone
from typing import Any

MAX_DEBUG_TEXT_CHARS = 8192
SENSITIVE_EXACT_KEYS = {
    "authorization",
    "token",
    "access_token",
    "refresh_token",
    "auth_token",
    "api_key",
    "apikey",
    "secret",
    "password",
    "key",
}


def _is_sensitive_key(key: str) -> bool:
    lower_key = str(key).lower()
    if "authorization" in lower_key:
        return True
    if lower_key in SENSITIVE_EXACT_KEYS:
        return True

    # 拆分字段名，避免把 max_tokens / keywords 这类误判为敏感字段
    tokens = [t for t in re.split(r"[^a-z0-9]+|_+", lower_key) if t]
    if not tokens:
        return False

    # token（单数）属于敏感；tokens（复数）通常是计数字段
    if "token" in tokens and "tokens" not in tokens:
        return True
    if "secret" in tokens or "password" in tokens:
        return True
    if "key" in tokens and any(t in tokens for t in ("api", "auth", "access", "secret")):
        return True

    return False


def _mask_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        if len(value) <= 6:
            return "***"
        return value[:3] + "***" + value[-3:]
    return "***"


def mask_sensitive(data: Any) -> Any:
    if isinstance(data, dict):
        masked: dict[str, Any] = {}
        for key, value in data.items():
            if _is_sensitive_key(str(key)):
                masked[key] = _mask_value(value)
            else:
                masked[key] = mask_sensitive(value)
        return masked
    if isinstance(data, list):
        return [mask_sensitive(item) for item in data]
    return data


def _truncate_text(text: str, max_chars: int = MAX_DEBUG_TEXT_CHARS) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars], True


def build_debug_info(
    request_info: dict[str, Any],
    response_text: str | None = None,
    response_json: Any = None,
    max_chars: int = MAX_DEBUG_TEXT_CHARS,
) -> dict[str, Any]:
    safe_request = mask_sensitive(request_info)
    safe_response_json = mask_sensitive(response_json) if response_json is not None else None

    raw_text = response_text or ""
    truncated_text, truncated = _truncate_text(raw_text, max_chars=max_chars)

    # 避免 raw_json 过大导致响应暴涨，这里用字符串长度做一次轻量保护
    json_too_large = False
    if safe_response_json is not None:
        try:
            serialized = json.dumps(safe_response_json, ensure_ascii=False)
            if len(serialized) > max_chars * 2:
                safe_response_json = {
                    "_truncated": True,
                    "preview": serialized[: max_chars * 2],
                }
                json_too_large = True
        except Exception:
            safe_response_json = {"_unserializable": True}

    return {
        "request": safe_request,
        "response": {
            "raw_text": truncated_text if raw_text else None,
            "raw_json": safe_response_json,
        },
        "meta": {
            "truncated": truncated or json_too_large,
            "max_chars": max_chars,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
    }
