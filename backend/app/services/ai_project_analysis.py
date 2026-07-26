from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.ai_calls import generate_structured_with_client
from app.domain.references import coerce_setting_reference_type
from app.services.ai_timeouts import ai_text_timeout_seconds
from app.services.ai_workloads import (
    AITextWorkload,
    ai_text_workload_policy,
)
from app.services.ai_types import AITextClient


class _ProjectAnalysisCharacterOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    gender: str | None = None
    identity: str | None = None
    personality: list[str] = Field(default_factory=list)
    appearance: str | None = None
    relationships: str | None = None
    summary: str | None = None


class _ProjectAnalysisOutlineOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    content: str = ""


class _ProjectAnalysisSettingOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    type: str = "note"
    content: str = ""


class _ProjectAnalysisOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    characters: list[_ProjectAnalysisCharacterOutput] = Field(default_factory=list)
    outlines: list[_ProjectAnalysisOutlineOutput] = Field(default_factory=list)
    settings: list[_ProjectAnalysisSettingOutput] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_content(self) -> "_ProjectAnalysisOutput":
        if not (self.characters or self.outlines or self.settings):
            raise ValueError("project analysis is empty")
        return self


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

    policy = ai_text_workload_policy(AITextWorkload.GENERATION_STRUCTURED)
    generated = await generate_structured_with_client(
        client,
        system_prompt,
        user_prompt,
        output_model=_ProjectAnalysisOutput,
        requested_model=getattr(client, "model_name", None),
        temperature=temp,
        max_tokens=policy.max_tokens,
        timeout=ai_text_timeout_seconds(policy.timeout_profile),
        max_attempts=policy.max_attempts,
    )
    result = normalize_project_analysis(generated.value.model_dump())
    if debug:
        result["debug_info"] = client._wrap_debug(
            system_prompt,
            user_prompt,
            temp,
            policy.max_tokens,
            generated.response.content,
            generated.response.response_json,
        )
    return result
