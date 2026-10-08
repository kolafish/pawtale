from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
from uuid import uuid4

import httpx

from app.config import DATA_DIR, get_settings
from app.services.provider_limits import provider_concurrency_limiter
from app.services.ai_credits import assert_ai_credit_available
from app.services.ai_costs import estimate_image_cost
from app.services.ai_usage_records import AIUsageContext, record_ai_usage


DEFAULT_SHARE_VIDEO_BACKGROUND_STYLE = "watercolor"
DEFAULT_SHARE_VIDEO_BACKGROUND_MODE = "auto"
SHARE_VIDEO_BACKGROUND_MODE_SINGLE = "single"
SHARE_VIDEO_BACKGROUND_MODE_SMART_MULTI = "smart_multi"
SHARE_VIDEO_BACKGROUND_PROMPT_VERSION = "memoir-share-video-background-v9"
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


_VIDEO_BACKGROUND_PROMPT = """根据以下口述内容，创作一张竖版 9:16 的短视频画面：

{content}
{narrator}{story_context}
{rules}"""

_VIDEO_BACKGROUND_SET_PROMPT = """根据同一段口述，生成一组共 {count} 张竖版 9:16 的短视频画面。这组图按顺序对应下面 {count} 个片段，一个片段一张图。

整段口述：
{story_text}
{narrator}{visual_context}
这是同一段回忆里的连续画面：同一个人物在各张图里保持一致的长相、性别、年龄、发型和穿着，环境和年代前后一致；每张图的场景、构图、镜头角度和人物动作按各自片段重新设计，不要画成几张差不多的图。

{scenes}

{rules}"""

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
        value="pencil",
        label="柔和铅笔",
        description="细腻铅笔线条和轻柔明暗，适合人物关系与安静日常",
        drawing_instruction=(
            "使用柔和的石墨铅笔绘画风格，以细腻线条、轻柔排线和适度明暗塑造场景。"
            "可以根据原文氛围加入少量低浓度彩铅，纸面干净自然，保留明确的手绘笔触。"
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
        value="gouache",
        label="叙事水粉",
        description="明确色块和柔和笔触，适合具体事件与富有生活气息的场景",
        drawing_instruction=(
            "使用叙事水粉画风格，以明确而协调的色块、柔和笔触和概括但可信的造型"
            "表现人物、房间、街道与生活物件。画面自然、有层次，色彩和明度跟随原文氛围。"
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

# Historical jobs and image assets persist style IDs. Keep the removed option
# readable for retries without presenting it for new generation.
_LEGACY_BACKGROUND_STYLES: tuple[ShareVideoBackgroundStyle, ...] = (
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
        story_text=_prompt_content(story),
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


def _narrator_block(narrator_context: str, background_note: str = "") -> str:
    block = ""
    note = re.sub(r"\s+", " ", background_note.strip())
    if note:
        block += f"\n用户补充的人物背景（人物性别、年龄和关系以此为准）：{note}\n"
    narrator = narrator_context.strip()
    if narrator:
        block += f"\n讲述者资料（人物性别、年龄先从口述内容推断，内容里看不出来时再参考）：{narrator}\n"
    return block


def _prompt_result(style: ShareVideoBackgroundStyle, content: str, prompt: str) -> ShareVideoBackgroundPrompt:
    prompt_hash = hashlib.sha256(
        "\n".join((
            SHARE_VIDEO_BACKGROUND_PROMPT_VERSION,
            style.value,
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
) -> ShareVideoBackgroundPrompt:
    style = share_video_background_style(style_id)
    content = _prompt_content(selected_text)
    prompt = _VIDEO_BACKGROUND_PROMPT.format(
        content=content,
        narrator=_narrator_block(narrator_context, background_note),
        story_context=_story_context(story_text, visual_context),
        rules=_VIDEO_BACKGROUND_RULES.format(drawing_instruction=style.drawing_instruction),
    ).strip()
    return _prompt_result(style, content, prompt)


def build_share_video_background_set_prompt(
    scene_texts: list[str],
    style_id: str | None,
    *,
    story_text: str,
    visual_context: str = "",
    narrator_context: str = "",
    background_note: str = "",
) -> ShareVideoBackgroundPrompt:
    """One prompt for a consistent set of images, one per scene, in order."""
    style = share_video_background_style(style_id)
    content = "\n".join(
        f"第 {index + 1} 张：{_prompt_content(text)}" for index, text in enumerate(scene_texts)
    )
    visual = re.sub(r"\s+", " ", (visual_context or "").strip())
    prompt = _VIDEO_BACKGROUND_SET_PROMPT.format(
        count=len(scene_texts),
        story_text=_prompt_content(story_text),
        narrator=_narrator_block(narrator_context, background_note),
        visual_context=_VIDEO_BACKGROUND_VISUAL_CONTEXT.format(visual_context=visual) if visual else "",
        scenes=content,
        rules=_VIDEO_BACKGROUND_RULES.format(drawing_instruction=style.drawing_instruction),
    ).strip()
    return _prompt_result(style, content, prompt)


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

    def __init__(self) -> None:
        settings = get_settings()
        self.api_key = settings.volc_ark_api_key.strip()
        self.base_url = settings.volc_ark_base_url.rstrip("/")
        self.model = settings.volc_ark_image_model.strip()
        self.size = settings.volc_ark_image_size.strip()
        self.output_format = settings.volc_ark_image_output_format.strip().lower() or "png"
        self.response_format = settings.volc_ark_image_response_format.strip() or "b64_json"
        self.timeout_seconds = settings.volc_ark_image_timeout_seconds
        self.max_concurrent_requests = settings.volc_ark_image_max_concurrent_requests

    async def generate(self, prompt: str) -> VolcArkGeneratedImage:
        images = await self._generate(prompt, {}, self.timeout_seconds)
        if not images or images[0] is None:
            raise ShareVideoBackgroundGenerationError("火山图片生成没有可用图片数据")
        return images[0]

    async def generate_set(self, prompt: str, count: int) -> list[VolcArkGeneratedImage | None]:
        """Ark group generation: up to `count` images of the same people in one request."""
        return await self._generate(
            prompt,
            {
                "sequential_image_generation": "auto",
                "sequential_image_generation_options": {"max_images": count},
            },
            self.timeout_seconds * max(1, count),
        )

    async def _generate(
        self,
        prompt: str,
        extra_payload: dict[str, object],
        timeout_seconds: float,
    ) -> list[VolcArkGeneratedImage | None]:
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
            **extra_payload,
        }

        async def request() -> list[VolcArkGeneratedImage | None]:
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
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
                images: list[VolcArkGeneratedImage | None] = []
                for item in data:
                    content = await self._extract_image_content(client, item)
                    images.append(
                        VolcArkGeneratedImage(
                            content=content,
                            mime_type=f"image/{self.output_format}",
                            model=model,
                            usage=usage,
                        )
                        if content
                        else None
                    )
                return images

        return await provider_concurrency_limiter.run(
            "ai_image",
            self.max_concurrent_requests,
            request,
        )

    async def _extract_image_content(self, client: httpx.AsyncClient, item: object) -> bytes | None:
        # Group generation reports a failed image as an item without image data.
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
    usage_context: AIUsageContext | None = None,
) -> ShareVideoBackgroundAsset | None:
    settings = get_settings()
    normalized_style = normalize_share_video_background_style(style_id)
    if not settings.memoir_share_video_ai_background_enabled:
        return None
    if not settings.volc_ark_api_key.strip():
        return None

    prompt = build_share_video_background_prompt(
        selected_text,
        normalized_style,
        story_text=story_text,
        visual_context=visual_context,
        narrator_context=narrator_context,
        background_note=background_note,
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
        image = await VolcArkImageGenerationService().generate(prompt.prompt)
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


async def get_or_generate_share_video_background_set(
    scene_texts: list[str],
    style_id: str | None,
    *,
    story_text: str,
    visual_context: str = "",
    narrator_context: str = "",
    background_note: str = "",
    usage_context: AIUsageContext | None = None,
) -> list[ShareVideoBackgroundAsset | None] | None:
    """Generate every scene image in one group request so the people stay the same.

    Returns one entry per scene; an entry is None when the provider returned fewer
    images than asked, so the caller can draw that scene on its own.
    """
    settings = get_settings()
    normalized_style = normalize_share_video_background_style(style_id)
    if not settings.memoir_share_video_ai_background_enabled:
        return None
    if not settings.volc_ark_api_key.strip():
        return None

    prompt = build_share_video_background_set_prompt(
        scene_texts,
        normalized_style,
        story_text=story_text,
        visual_context=visual_context,
        narrator_context=narrator_context,
        background_note=background_note,
    )
    extension = (settings.volc_ark_image_output_format.strip().lower() or "png").lstrip(".")
    model = settings.volc_ark_image_model.strip()
    storage_paths = [
        _background_storage_path(prompt.style.value, model, f"{prompt.prompt_hash}-{index + 1}", extension)
        for index in range(len(scene_texts))
    ]
    paths = [_background_file_path(storage_path) for storage_path in storage_paths]

    def asset(index: int, asset_model: str, cache_hit: bool) -> ShareVideoBackgroundAsset:
        return ShareVideoBackgroundAsset(
            path=paths[index],
            storage_path=storage_paths[index],
            style_id=prompt.style.value,
            prompt_hash=f"{prompt.prompt_hash}-{index + 1}",
            prompt_template_version=prompt.template_version,
            provider=VolcArkImageGenerationService.provider_name,
            model=asset_model,
            cache_hit=cache_hit,
        )

    if all(path.exists() and path.stat().st_size > 0 for path in paths):
        return [asset(index, model, True) for index in range(len(paths))]

    try:
        if usage_context and usage_context.user_id:
            await assert_ai_credit_available(usage_context.user_id)
        images = await VolcArkImageGenerationService().generate_set(prompt.prompt, len(scene_texts))
    except Exception as exc:
        await record_ai_usage(
            provider=VolcArkImageGenerationService.provider_name,
            model=model or "unknown",
            status="failed",
            cost=estimate_image_cost(
                provider=VolcArkImageGenerationService.provider_name,
                model=model,
                image_count=len(scene_texts),
            ),
            image_count=len(scene_texts),
            error_type=type(exc).__name__,
            context=usage_context,
        )
        raise
    generated = [image for image in images[: len(scene_texts)] if image is not None]
    if generated:
        await record_ai_usage(
            provider=VolcArkImageGenerationService.provider_name,
            model=generated[0].model,
            status="succeeded",
            cost=estimate_image_cost(
                provider=VolcArkImageGenerationService.provider_name,
                model=generated[0].model,
                image_count=len(generated),
            ),
            image_count=len(generated),
            context=usage_context,
        )
    assets: list[ShareVideoBackgroundAsset | None] = []
    for index in range(len(scene_texts)):
        image = images[index] if index < len(images) else None
        if image is None:
            assets.append(None)
            continue
        _write_background(paths[index], image.content)
        assets.append(asset(index, image.model, False))
    return assets
