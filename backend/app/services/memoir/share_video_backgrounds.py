from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
from io import BytesIO
from pathlib import Path
import re
from uuid import uuid4

import httpx
from PIL import Image

from app.config import DATA_DIR, get_settings
from app.services.provider_limits import provider_concurrency_limiter
from app.services.ai_credits import assert_ai_credit_available
from app.services.ai_costs import estimate_image_cost
from app.services.ai_usage_records import AIUsageContext, record_ai_usage


DEFAULT_SHARE_VIDEO_BACKGROUND_STYLE = "watercolor"
DEFAULT_SHARE_VIDEO_BACKGROUND_MODE = "auto"
SHARE_VIDEO_BACKGROUND_MODE_SINGLE = "single"
SHARE_VIDEO_BACKGROUND_MODE_SMART_MULTI = "smart_multi"
SHARE_VIDEO_BACKGROUND_PROMPT_VERSION = "memoir-share-video-background-v10"
SHARE_VIDEO_BACKGROUND_CONTENT_MAX_CHARS = 300
MEMOIR_SHARE_VIDEO_BACKGROUND_DIR = DATA_DIR / "memoir" / "share-video-backgrounds"


@dataclass(frozen=True)
class ShareVideoBackgroundStyle:
    value: str
    label: str
    description: str
    drawing_instruction: str


@dataclass(frozen=True)
class ShareVideoBackgroundPrompt:
    style: ShareVideoBackgroundStyle
    content: str
    prompt: str
    prompt_hash: str
    template_version: str


@dataclass(frozen=True)
class ShareVideoBackgroundAsset:
    path: Path
    storage_path: str
    style_id: str
    prompt_hash: str
    prompt_template_version: str
    provider: str
    model: str
    cache_hit: bool


@dataclass(frozen=True)
class VolcArkGeneratedImage:
    content: bytes
    mime_type: str
    model: str
    usage: dict[str, object]


class ShareVideoBackgroundGenerationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ShareVideoImageReference:
    data_url: str
    digest: str

    @classmethod
    def from_path(cls, path: Path) -> "ShareVideoImageReference":
        try:
            with Image.open(path) as source:
                source.thumbnail((1400, 1600))
                output = BytesIO()
                source.convert("RGB").save(output, format="JPEG", quality=92)
        except (Image.DecompressionBombError, OSError) as exc:
            raise ShareVideoBackgroundGenerationError("无法读取分享视频人物参考图") from exc
        content = output.getvalue()
        return cls(
            data_url="data:image/jpeg;base64," + base64.b64encode(content).decode("ascii"),
            digest=hashlib.sha256(content).hexdigest(),
        )


_VIDEO_BACKGROUND_PROMPT = """根据以下口述内容，创作一张竖版 9:16 的短视频画面：

{content}
{narrator}{story_context}
{rules}"""

_CAST_REFERENCE_PROMPT = """为这段家庭口述中的{subjects}画一张多人定稿图，供同一视频的各镜头参考。
每位列出的人物只画一次，中近景并列，面部清楚、衣着可辨，背景简洁。只画列出的人物，画中不写名字或任何文字。
{narrator}
口述背景：{story_text}
人物设定：{visual_context}
{drawing_instruction}"""

_REFERENCE_INSTRUCTION = "参考图供辨认再次出场的人物和画风。保留他们的脸、年龄、发型与衣着。按本镜原话和拍法重新构图，只画此刻出场的人。"

_VIDEO_BACKGROUND_RULES = """画面内容：
- 从原文中选择一个具体、可见的回忆瞬间，不把不同时间或地点的事件拼贴在一起。
- 优先呈现原文明确提到的时间、地点、人物关系、动作和关键物件，让这些细节成为画面主体；不要用泛泛的漂亮风景代替讲述的场景。
- 原文没有说明的年代、地点、人物身份或生活细节不要猜测；缺少可见细节时，以克制的环境和光线衬托原文情绪。

绘画风格：
{drawing_instruction}

竖版构图：
- 画面主体（人物、关键物件、场景焦点）安排在画面上方约六成的区域内，画面要饱满，不留大块空白。
- 画面下方约三分之一是自然、安静、低细节的延伸，例如地面、石板路、桌面、水面、草地或阴影，后期会在这里叠加口述字幕；不生成生硬的纯色空块。
- 下方三分之一不放人物面部、文字状物体或视觉焦点。
- 主体四周留出一些余量，后期会做缓慢的推拉和平移镜头。

生成边界：
- 只呈现原文支持的场景、人物关系和生活细节，不添加无依据的重要人物、事件、地点或时代符号。
- 不出现文字、字幕、题字、印章、水印、logo 或现代 UI 元素。
- 避免畸形人物、塑料质感、过度锐化、过度饱和和夸张电影光效。"""

_VIDEO_BACKGROUND_STORY_CONTEXT = """
这张画面是同一段视频里的其中一张，上面只是其中一部分。整段口述如下，用来理解“我”“他”等称呼指的是谁、事情发生在哪里和什么年代；画面只表现上面那部分内容：

{story_text}
{visual_context}"""

_VIDEO_BACKGROUND_VISUAL_CONTEXT = """
各画面共用的人物和场景设定，人物的年龄、外貌、穿着和环境要与同一视频的其他画面保持一致：
{visual_context}
"""

_SELECTABLE_BACKGROUND_STYLES: tuple[ShareVideoBackgroundStyle, ...] = (
    ShareVideoBackgroundStyle(
        value="watercolor",
        label="清透水彩",
        description="透明水色和柔和叠染，适合大多数家庭生活与人物故事",
        drawing_instruction=(
            "使用清透自然的水彩绘画风格，通过透明水色、柔和叠染和可见的手绘边缘"
            "表现真实生活场景。画面保持干净、通透、有生活气息，光线和色彩跟随原文氛围。"
        ),
    ),
    ShareVideoBackgroundStyle(
        value="ink_paper",
        label="水墨淡彩",
        description="现代水墨与透明淡彩，适合含蓄、平静和富有东方气韵的内容",
        drawing_instruction=(
            "使用现代水墨淡彩绘画风格，以自然墨色层次、简洁线条和少量透明淡彩"
            "表现人物与生活场景。画面含蓄而清晰，纸面干净自然，色彩跟随原文氛围。"
        ),
    ),
    ShareVideoBackgroundStyle(
        value="old_album",
        label="怀旧影像",
        description="旧相册手工上色质感，适合原文本身具有明确年代感的内容",
        drawing_instruction=(
            "使用旧家庭相册启发的手工上色绘画风格，带有银盐胶片颗粒、轻微褪色"
            "和水彩补色质感。保留明显的绘画笔触，画面真实、安静、低对比。"
        ),
    ),
)

# Historical jobs and image assets persist style IDs. Keep removed options
# readable for retries without presenting it for new generation.
_LEGACY_BACKGROUND_STYLES: tuple[ShareVideoBackgroundStyle, ...] = (
    ShareVideoBackgroundStyle(
        value="pencil",
        label="柔和铅笔",
        description="细腻铅笔线条和轻柔明暗，适合人物关系与安静日常",
        drawing_instruction=(
            "使用柔和的石墨铅笔绘画风格，以细腻线条、轻柔排线和适度明暗塑造场景。"
            "可以根据原文氛围加入少量低浓度彩铅，纸面干净自然，保留明确的手绘笔触。"
        ),
    ),
    ShareVideoBackgroundStyle(
        value="gouache",
        label="叙事水粉",
        description="明确色块和柔和笔触，适合具体事件与富有生活气息的场景",
        drawing_instruction=(
            "使用叙事水粉画风格，以明确而协调的色块、柔和笔触和概括但可信的造型"
            "表现人物、房间、街道与生活物件。画面自然、有层次，色彩和明度跟随原文氛围。"
        ),
    ),
    ShareVideoBackgroundStyle(
        value="storybook",
        label="老故事书",
        description="历史任务使用的老连环画和旧书插图风格",
        drawing_instruction=(
            "使用二十世纪中国老连环画与旧书插图绘画风格，以细密钢笔线描、"
            "朴素写实的叙事造型和少量做旧套色表现生活场景。"
        ),
    ),
)
_BACKGROUND_STYLE_BY_VALUE = {
    style.value: style
    for style in (*_SELECTABLE_BACKGROUND_STYLES, *_LEGACY_BACKGROUND_STYLES)
}

_BACKGROUND_MODES: tuple[dict[str, str], ...] = (
    {
        "value": DEFAULT_SHARE_VIDEO_BACKGROUND_MODE,
        "label": "自动",
        "description": "根据内容里场景的变化决定用几张图",
    },
    {
        "value": SHARE_VIDEO_BACKGROUND_MODE_SINGLE,
        "label": "单张背景",
        "description": "整段视频使用一张背景图，生成更快",
    },
    {
        "value": SHARE_VIDEO_BACKGROUND_MODE_SMART_MULTI,
        "label": "智能多图",
        "description": "根据口述内容拆分多个画面，视频中自动切换背景",
    },
)
_BACKGROUND_MODE_VALUES = {mode["value"] for mode in _BACKGROUND_MODES}


def share_video_background_style_options() -> list[dict[str, str]]:
    return [
        {
            "value": style.value,
            "label": style.label,
            "description": style.description,
        }
        for style in _SELECTABLE_BACKGROUND_STYLES
    ]


def share_video_background_mode_options() -> list[dict[str, str]]:
    return [dict(mode) for mode in _BACKGROUND_MODES]


def normalize_share_video_background_style(value: str | None) -> str:
    normalized = (value or DEFAULT_SHARE_VIDEO_BACKGROUND_STYLE).strip()
    if normalized not in _BACKGROUND_STYLE_BY_VALUE:
        valid = "、".join(style.label for style in _SELECTABLE_BACKGROUND_STYLES)
        raise ValueError(f"未知的分享视频背景风格，可选：{valid}")
    return normalized


def normalize_share_video_background_mode(value: str | None) -> str:
    normalized = (value or DEFAULT_SHARE_VIDEO_BACKGROUND_MODE).strip()
    if normalized not in _BACKGROUND_MODE_VALUES:
        valid = "、".join(mode["label"] for mode in _BACKGROUND_MODES)
        raise ValueError(f"未知的分享视频背景模式，可选：{valid}")
    return normalized


def share_video_background_style_label(style_id: str | None) -> str:
    """The illustration style's name for display; empty for a style no longer offered."""
    style = _BACKGROUND_STYLE_BY_VALUE.get((style_id or "").strip())
    return style.label if style else ""


def share_video_background_style(style_id: str | None) -> ShareVideoBackgroundStyle:
    normalized = normalize_share_video_background_style(style_id)
    return _BACKGROUND_STYLE_BY_VALUE[normalized]


def _prompt_content(text: str) -> str:
    normalized = re.sub(r"\s+", " ", (text or "").strip())
    if not normalized:
        return "这段口述内容没有可用文字，请生成温暖、克制、留白充足的家庭回忆录背景。"
    characters = list(normalized)
    if len(characters) <= SHARE_VIDEO_BACKGROUND_CONTENT_MAX_CHARS:
        return normalized
    return "".join(characters[:SHARE_VIDEO_BACKGROUND_CONTENT_MAX_CHARS]).rstrip() + "..."


def _story_context(story_text: str, visual_context: str) -> str:
    story = re.sub(r"\s+", " ", (story_text or "").strip())
    if not story:
        return ""
    visual = re.sub(r"\s+", " ", (visual_context or "").strip())
    return _VIDEO_BACKGROUND_STORY_CONTEXT.format(
        story_text=story,
        visual_context=_VIDEO_BACKGROUND_VISUAL_CONTEXT.format(visual_context=visual) if visual else "",
    )


_NARRATOR_GENDER_LABELS = {"male": "男性", "female": "女性"}


def share_video_narrator_context(profile: dict[str, object] | None) -> str:
    """Who "我" is, from the project's narrator profile, for image and scene prompts."""
    profile = profile if isinstance(profile, dict) else {}
    parts: list[str] = []
    gender = _NARRATOR_GENDER_LABELS.get(str(profile.get("narrator_gender") or ""))
    if gender:
        parts.append(f"讲述者“我”是{gender}")
    age = profile.get("narrator_age")
    if isinstance(age, int) and age > 0:
        parts.append(f"现在约 {age} 岁，回忆里的年纪按故事发生的时间推算")
    origin = str(profile.get("narrator_origin") or "").strip()
    if origin:
        parts.append(f"家乡在{origin}")
    return "；".join(parts) + "。" if parts else ""


def image_narrator_block(narrator_context: str, background_note: str = "") -> str:
    block = ""
    note = re.sub(r"\s+", " ", background_note.strip())
    if note:
        block += f"\n用户补充的人物背景（人物性别、年龄和关系以此为准）：{note}\n"
    narrator = narrator_context.strip()
    if narrator:
        block += f"\n讲述者资料（人物性别、年龄先从口述内容推断，内容里看不出来时再参考）：{narrator}\n"
    return block


def _prompt_result(
    style: ShareVideoBackgroundStyle, content: str, prompt: str, reference_digest: str = "",
) -> ShareVideoBackgroundPrompt:
    prompt_hash = hashlib.sha256(
        "\n".join((
            SHARE_VIDEO_BACKGROUND_PROMPT_VERSION,
            style.value,
            reference_digest,
            prompt,
        )).encode("utf-8")
    ).hexdigest()
    return ShareVideoBackgroundPrompt(
        style=style,
        content=content,
        prompt=prompt,
        prompt_hash=prompt_hash,
        template_version=SHARE_VIDEO_BACKGROUND_PROMPT_VERSION,
    )


def build_share_video_background_prompt(
    selected_text: str,
    style_id: str | None,
    *,
    story_text: str = "",
    visual_context: str = "",
    narrator_context: str = "",
    background_note: str = "",
    shot_direction: str = "",
    reference: ShareVideoImageReference | None = None,
) -> ShareVideoBackgroundPrompt:
    style = share_video_background_style(style_id)
    content = _prompt_content(selected_text)
    prompt = _VIDEO_BACKGROUND_PROMPT.format(
        content=content,
        narrator=image_narrator_block(narrator_context, background_note),
        story_context=_story_context(story_text, visual_context),
        rules=_VIDEO_BACKGROUND_RULES.format(drawing_instruction=style.drawing_instruction),
    ).strip()
    if reference:
        prompt += f"\n{_REFERENCE_INSTRUCTION}"
    if shot_direction:
        prompt += (
            f"\n本镜原话：{content}\n本镜拍法：{shot_direction}\n"
            "按本镜原话确定谁在场、发生什么和哪些物件入画。拍法只用于取景、视角和主体位置。"
            "拍法中如出现原话和用户补充都没提到的细节（例如工作证、标语、年份），请省略。"
        )
    return _prompt_result(style, content, prompt, reference.digest if reference else "")


def _safe_path_part(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip())
    return normalized.strip("-") or "default"


def _background_storage_path(style_id: str, model: str, prompt_hash: str, extension: str) -> str:
    return (
        "memoir/share-video-backgrounds/"
        f"{_safe_path_part(style_id)}/{_safe_path_part(model)}/{prompt_hash}.{extension}"
    )


def _background_file_path(storage_path: str) -> Path:
    relative = Path(storage_path).relative_to(Path("memoir") / "share-video-backgrounds")
    return MEMOIR_SHARE_VIDEO_BACKGROUND_DIR / relative


def _decode_base64_image(value: str) -> bytes:
    payload = value.split(",", 1)[1] if value.startswith("data:") and "," in value else value
    try:
        return base64.b64decode(payload, validate=False)
    except Exception as exc:
        raise ShareVideoBackgroundGenerationError("火山图片生成返回了无效图片数据") from exc


class VolcArkImageGenerationService:
    provider_name = "volc_ark"

    def __init__(self, *, model: str | None = None) -> None:
        settings = get_settings()
        self.api_key = settings.volc_ark_api_key.strip()
        self.base_url = settings.volc_ark_base_url.rstrip("/")
        self.model = (model or settings.volc_ark_image_model).strip()
        self.size = settings.volc_ark_image_size.strip()
        self.output_format = settings.volc_ark_image_output_format.strip().lower() or "png"
        self.response_format = settings.volc_ark_image_response_format.strip() or "b64_json"
        self.timeout_seconds = settings.volc_ark_image_timeout_seconds
        self.max_concurrent_requests = settings.volc_ark_image_max_concurrent_requests

    async def generate(self, prompt: str, *, reference_url: str | None = None) -> VolcArkGeneratedImage:
        if not self.api_key:
            raise ShareVideoBackgroundGenerationError("火山方舟 API Key 未配置")
        if not self.model:
            raise ShareVideoBackgroundGenerationError("火山方舟图片模型未配置")

        payload = {
            "model": self.model,
            "prompt": prompt,
            "size": self.size,
            "response_format": self.response_format,
            "output_format": self.output_format,
            "watermark": False,
        }

        if reference_url:
            payload["image"] = reference_url

        async def request() -> VolcArkGeneratedImage:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                try:
                    response = await client.post(
                        f"{self.base_url}/images/generations",
                        headers={
                            "Authorization": f"Bearer {self.api_key}",
                            "Content-Type": "application/json",
                        },
                        json=payload,
                    )
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    status_code = exc.response.status_code
                    raise ShareVideoBackgroundGenerationError(
                        f"火山图片生成失败，HTTP {status_code}"
                    ) from exc
                except httpx.HTTPError as exc:
                    raise ShareVideoBackgroundGenerationError("火山图片生成请求失败") from exc

                try:
                    result = response.json()
                except ValueError as exc:
                    raise ShareVideoBackgroundGenerationError("火山图片生成返回格式无效") from exc
                data = result.get("data")
                if not isinstance(data, list) or not data:
                    raise ShareVideoBackgroundGenerationError("火山图片生成没有返回图片")
                usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
                model = str(result.get("model") or self.model)
                content = await self._extract_image_content(client, data[0])
                if not content:
                    raise ShareVideoBackgroundGenerationError("火山图片生成没有可用图片数据")
                return VolcArkGeneratedImage(content, f"image/{self.output_format}", model, usage)

        return await provider_concurrency_limiter.run(
            "ai_image",
            self.max_concurrent_requests,
            request,
        )

    async def _extract_image_content(self, client: httpx.AsyncClient, item: object) -> bytes | None:
        if not isinstance(item, dict):
            return None

        b64_json = item.get("b64_json")
        if isinstance(b64_json, str) and b64_json.strip():
            return _decode_base64_image(b64_json.strip())

        url = item.get("url")
        if isinstance(url, str) and url.strip():
            try:
                response = await client.get(url.strip())
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise ShareVideoBackgroundGenerationError("火山图片下载失败") from exc
            return response.content

        return None


def _write_background(path: Path, content: bytes) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        temp_path.write_bytes(content)
        temp_path.replace(path)
    except OSError as exc:
        raise ShareVideoBackgroundGenerationError("分享视频背景图缓存写入失败") from exc


async def get_or_generate_share_video_background(
    selected_text: str,
    style_id: str | None,
    *,
    story_text: str = "",
    visual_context: str = "",
    narrator_context: str = "",
    background_note: str = "",
    shot_direction: str = "",
    reference: ShareVideoImageReference | None = None,
    cast_subjects: tuple[str, ...] = (),
    usage_context: AIUsageContext | None = None,
) -> ShareVideoBackgroundAsset | None:
    settings = get_settings()
    if not settings.memoir_share_video_ai_background_enabled or not settings.volc_ark_api_key.strip():
        return None

    if cast_subjects:
        style = share_video_background_style(style_id)
        prompt = _prompt_result(style, selected_text, _CAST_REFERENCE_PROMPT.format(
            subjects="、".join(cast_subjects),
            story_text=selected_text,
            narrator=image_narrator_block(narrator_context, background_note),
            visual_context=visual_context,
            drawing_instruction=style.drawing_instruction,
        ))
    else:
        prompt = build_share_video_background_prompt(
            selected_text, style_id,
            story_text=story_text, visual_context=visual_context,
            narrator_context=narrator_context, background_note=background_note,
            shot_direction=shot_direction, reference=reference,
        )
    extension = (settings.volc_ark_image_output_format.strip().lower() or "png").lstrip(".")
    model = settings.volc_ark_image_model.strip()
    storage_path = _background_storage_path(prompt.style.value, model, prompt.prompt_hash, extension)
    path = _background_file_path(storage_path)
    if path.exists() and path.stat().st_size > 0:
        return ShareVideoBackgroundAsset(
            path=path,
            storage_path=storage_path,
            style_id=prompt.style.value,
            prompt_hash=prompt.prompt_hash,
            prompt_template_version=prompt.template_version,
            provider=VolcArkImageGenerationService.provider_name,
            model=model,
            cache_hit=True,
        )

    try:
        if usage_context and usage_context.user_id:
            await assert_ai_credit_available(usage_context.user_id)
        image = await VolcArkImageGenerationService().generate(prompt.prompt, reference_url=reference.data_url if reference else None)
    except Exception as exc:
        await record_ai_usage(
            provider=VolcArkImageGenerationService.provider_name,
            model=model or "unknown",
            status="failed",
            cost=estimate_image_cost(
                provider=VolcArkImageGenerationService.provider_name,
                model=model,
                image_count=1,
            ),
            image_count=1,
            error_type=type(exc).__name__,
            context=usage_context,
        )
        raise
    await record_ai_usage(
        provider=VolcArkImageGenerationService.provider_name,
        model=image.model,
        status="succeeded",
        cost=estimate_image_cost(
            provider=VolcArkImageGenerationService.provider_name,
            model=image.model,
            image_count=1,
        ),
        image_count=1,
        context=usage_context,
    )
    _write_background(path, image.content)
    return ShareVideoBackgroundAsset(
        path=path,
        storage_path=storage_path,
        style_id=prompt.style.value,
        prompt_hash=prompt.prompt_hash,
        prompt_template_version=prompt.template_version,
        provider=VolcArkImageGenerationService.provider_name,
        model=image.model,
        cache_hit=False,
    )
