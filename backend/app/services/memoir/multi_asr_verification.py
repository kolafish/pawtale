from __future__ import annotations

from dataclasses import dataclass

from app.services.ai_calls import generate_text_with_client
from app.services.ai_factory import get_ai_service
from app.services.ai_timeouts import ai_text_timeout_seconds
from app.services.ai_types import AITextClient
from app.services.ai_workloads import AITextWorkload, ai_text_workload_policy
from app.services.memoir.asr_text_normalization import normalize_memoir_asr_text
from app.services.memoir.dialect_profile import (
    dialect_verification_guidance,
    resolve_memoir_dialect_profile,
)
from app.services.memoir.offline_asr import OfflineASRGlossaryEntry, OfflineASRResult


SYSTEM_PROMPT = "你是严谨的中文口述史转写校对编辑。"


@dataclass(frozen=True)
class MultiASRVerificationRequest:
    asr_results: tuple[OfflineASRResult, ...]
    question_context: str = ""
    original_raw: str = ""
    narrator_origin: str = ""
    dialect_glossary: tuple[OfflineASRGlossaryEntry, ...] = ()


@dataclass(frozen=True)
class VerifiedASRTranscript:
    text: str
    provider: str
    requested_model: str | None = None
    resolved_model: str | None = None
    response_model: str | None = None


def _asr_results_block(results: tuple[OfflineASRResult, ...]) -> str:
    lines: list[str] = []
    for index, result in enumerate(results, start=1):
        lines.append(f"ASR {index} provider={result.provider}：")
        lines.append(result.text.strip() or "无")
    return "\n".join(lines)


def build_multi_asr_verification_prompt(request: MultiASRVerificationRequest) -> str:
    original_raw = request.original_raw.strip() or "无"
    question = request.question_context.strip() or "无"
    origin = request.narrator_origin.strip()
    origin_description = f"来自{origin}，可能带当地口音" if origin else "可能带地方口音"
    dialect_guidance = dialect_verification_guidance(resolve_memoir_dialect_profile(origin))
    dialect_glossary = _dialect_glossary_block(request.dialect_glossary)
    return f"""下面是同一段中文口述的多份识别文本。说话人{origin_description}。

你收到的是文本，未收到音频。请比较识别结果，校正有充分文本依据的识别错误。

要求：
- 专名优先参考用户词表，并与识别文本核对；词表不证明人物做过何事。多路一致只是识别线索，不是事实保证。
- 姓名、数字、时间、亲属关系存在分歧且无法消解时，保留原识别中的说法和疑义；原识别为空时保留第一路的疑义，不拼凑一个更流畅的答案。
- 问题仅用于理解回答，未经讲述者确认的设定不能写入。保留否定、转述、情绪强度和模糊记忆；不将采访者或家人的话变成讲述者亲历。
{dialect_guidance}
- 如果某一路 ASR 出现明显无关英文、广告词、歌名或乱码，要结合其他结果判断，不要照抄。
- 去掉口头填充词、说话磕绊、赘词和没有信息增量的重复，但不要删掉有意义的细节。
- 保留人物、地名、时间、亲属称呼、单位和事件细节；不要新增事实，不要总结，不要文学化。
- 如果采访者的追问或家人插话被录进去，只有在它帮助理解回答时才用括号轻标；不要把采访问题当成讲述者正文扩写。
- 直接输出校对后的正文，不要标题、列表、解释或引号。

采访问题或近邻上下文：
{question}

当前系统里原有实时 ASR：
{original_raw}

项目方言与专名词表：
{dialect_glossary}

多模型离线 ASR：
{_asr_results_block(request.asr_results)}
"""


def _dialect_glossary_block(entries: tuple[OfflineASRGlossaryEntry, ...]) -> str:
    return "\n".join(f"{entry.source} => {entry.target}" for entry in entries) or "无"


def _strip_llm_wrapping(text: str) -> str:
    cleaned = text.strip()
    if len(cleaned) >= 2 and cleaned[0] in {"“", "「", '"', "'"} and cleaned[-1] in {"”", "」", '"', "'"}:
        return cleaned[1:-1].strip()
    return cleaned


async def verify_multi_asr_transcripts(
    request: MultiASRVerificationRequest,
    *,
    ai_model: str | None = None,
    ai_client: AITextClient | None = None,
) -> VerifiedASRTranscript:
    if not any(result.text.strip() for result in request.asr_results):
        raise ValueError("at least one non-empty ASR result is required")
    client = ai_client or get_ai_service(ai_model)
    policy = ai_text_workload_policy(AITextWorkload.GENERATION_TEXT)
    generated = await generate_text_with_client(
        client,
        SYSTEM_PROMPT,
        build_multi_asr_verification_prompt(request),
        requested_model=ai_model,
        temperature=0.1,
        max_tokens=policy.max_tokens,
        timeout=ai_text_timeout_seconds(policy.timeout_profile),
    )
    text = normalize_memoir_asr_text(
        _strip_llm_wrapping(generated.content)
    ).strip()
    if not text:
        raise ValueError("LLM returned empty corrected transcript")
    return VerifiedASRTranscript(
        text=text,
        provider=generated.provider or client.provider_name,
        requested_model=generated.requested_model,
        resolved_model=generated.resolved_model,
        response_model=generated.response_model,
    )
