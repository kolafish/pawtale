from __future__ import annotations

from dataclasses import dataclass

from app.services.ai_factory import get_ai_service
from app.services.ai_types import AITextClient
from app.services.memoir.asr_text_normalization import normalize_memoir_asr_text
from app.services.memoir.offline_asr import OfflineASRResult


SYSTEM_PROMPT = "你是严谨的中文口述史转写校对编辑。"


@dataclass(frozen=True)
class MultiASRVerificationRequest:
    asr_results: tuple[OfflineASRResult, ...]
    question_context: str = ""
    original_raw: str = ""
    original_edited: str = ""
    narrator_origin: str = ""


@dataclass(frozen=True)
class VerifiedASRTranscript:
    text: str
    provider: str


def _asr_results_block(results: tuple[OfflineASRResult, ...]) -> str:
    lines: list[str] = []
    for index, result in enumerate(results, start=1):
        lines.append(f"ASR {index} provider={result.provider}：")
        lines.append(result.text.strip() or "无")
    return "\n".join(lines)


def build_multi_asr_verification_prompt(request: MultiASRVerificationRequest) -> str:
    original_raw = request.original_raw.strip() or "无"
    original_edited = request.original_edited.strip() or "无"
    question = request.question_context.strip() or "无"
    origin = request.narrator_origin.strip()
    origin_description = f"来自{origin}，可能带当地口音" if origin else "可能带地方口音"
    dialect_guidance = _dialect_guidance(origin)
    return f"""下面是一位老年人的中文口述录音转写结果。说话人{origin_description}，语速慢、口头语多，可能夹杂家人插话和现场噪音。

请综合多个 ASR 模型结果，对这段口述做忠于原音频的校对。

要求：
- 只根据现有 ASR 文本互相印证，修正方言、口音、同音词、断句和明显误识别；无法确认时保留更可信的原表达。
{dialect_guidance}
- 如果某一路 ASR 出现明显无关英文、广告词、歌名或乱码，要结合其他结果判断，不要照抄。
- 可以去掉明显无意义的口头填充词和连续重复，比如“嗯、啊、那个那个、就是就是”。
- 保留人物、地名、时间、亲属称呼、单位和事件细节；不要新增事实，不要总结，不要文学化。
- 如果采访者的追问或家人插话被录进去，只有在它帮助理解回答时才用括号轻标；不要把采访问题当成讲述者正文扩写。
- 直接输出校对后的正文，不要标题、列表、解释或引号。

采访问题或近邻上下文：
{question}

当前系统里原有实时 ASR：
{original_raw}

当前系统里原有自动整理：
{original_edited}

多模型离线 ASR：
{_asr_results_block(request.asr_results)}
"""


def _dialect_guidance(origin: str) -> str:
    if any(region in origin for region in ("四川", "重庆")):
        return (
            "- 四川及重庆方言常见词包括“啥子、咋个、没得、晓得、屋头、婆婆、"
            "幺儿、得嘛、噻、喃、耍、不得”。"
        )
    if origin:
        return (
            f"- 说话人来自{origin}，遇到地方用语时优先结合多路 ASR 上下文判断，"
            "不要擅自改成普通话书面表达。"
        )
    return "- 遇到地方用语时优先结合多路 ASR 上下文判断，不要擅自改成普通话书面表达。"


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
    content = await client._call_llm(
        SYSTEM_PROMPT,
        build_multi_asr_verification_prompt(request),
        temperature=0.1,
        max_tokens=1800,
        timeout=120.0,
    )
    text = normalize_memoir_asr_text(_strip_llm_wrapping(str(content))).strip()
    if not text:
        raise ValueError("LLM returned empty corrected transcript")
    return VerifiedASRTranscript(text=text, provider=client.provider_name)
