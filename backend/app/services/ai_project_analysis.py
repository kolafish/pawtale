import asyncio
import logging

from app.domain.references import coerce_setting_reference_type
from app.services.ai_timeouts import (
    AIProviderTimeoutError,
    AITextTimeoutProfile,
    ai_text_timeout_seconds,
)
from app.services.ai_types import AITextClient
from app.services.json_utils import extract_json_payload


logger = logging.getLogger(__name__)


def normalize_project_analysis(parsed: object) -> dict:
    result: dict = {
        "characters": [],
        "outlines": [],
        "settings": [],
    }

    if not isinstance(parsed, dict):
        return result

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
            result["settings"].append({
                "name": str(setting["name"]).strip(),
                "type": coerce_setting_reference_type(setting.get("type")).value,
                "content": str(setting.get("content") or "").strip(),
            })

    return result


async def analyze_project_content(
    client: AITextClient,
    content: str,
    debug: bool = False,
) -> dict:
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

    max_attempts = 3
    timeout_seconds = ai_text_timeout_seconds(AITextTimeoutProfile.GENERATION)
    raw_text = ""
    response_json = None
    parsed: dict = {}
    last_error: Exception | None = None

    for attempt in range(max_attempts):
        try:
            raw_response = await client._call_llm(
                system_prompt,
                user_prompt,
                temperature=temp,
                max_tokens=3000,
                timeout=timeout_seconds,
                return_raw=debug,
            )
        except AIProviderTimeoutError:
            raise
        except Exception as e:
            last_error = e
            err_detail = str(e)
            if hasattr(e, "response") and e.response is not None:
                err_detail += f" | status={e.response.status_code} body={e.response.text[:500]}"
            logger.warning(
                "analyze_project_content attempt %d/%d [%s/%s]: %s",
                attempt + 1,
                max_attempts,
                client.provider_name,
                client.model_name,
                err_detail,
            )
            if attempt < max_attempts - 1:
                await asyncio.sleep(3 + attempt * 2)
            continue

        if debug:
            raw_text, response_json = raw_response  # type: ignore[misc]
        else:
            raw_text = raw_response  # type: ignore[assignment]
            response_json = None

        payload = extract_json_payload(raw_text)
        parsed = payload if isinstance(payload, dict) else {}
        has_data = bool(
            parsed.get("characters") or parsed.get("outlines") or parsed.get("settings")
        )
        if has_data:
            last_error = None
            break

        logger.warning(
            "analyze_project_content attempt %d/%d: JSON parse yielded empty result. raw_text[:200]=%s",
            attempt + 1,
            max_attempts,
            (raw_text or "")[:200],
        )
        if attempt < max_attempts - 1:
            await asyncio.sleep(2)

    if last_error is not None:
        raise last_error

    result = normalize_project_analysis(parsed)
    if debug:
        result["debug_info"] = client._wrap_debug(
            system_prompt,
            user_prompt,
            temp,
            4096,
            raw_text,
            response_json,
        )
    return result
