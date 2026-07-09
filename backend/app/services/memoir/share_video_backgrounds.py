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


DEFAULT_SHARE_VIDEO_BACKGROUND_STYLE = "old_album"
SHARE_VIDEO_BACKGROUND_PROMPT_VERSION = "memoir-share-video-background-v1"
SHARE_VIDEO_BACKGROUND_CONTENT_MAX_CHARS = 300
MEMOIR_SHARE_VIDEO_BACKGROUND_DIR = DATA_DIR / "memoir" / "share-video-backgrounds"


@dataclass(frozen=True)
class ShareVideoBackgroundStyle:
    value: str
    label: str
    description: str
    prompt_template: str


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


_OLD_ALBUM_PROMPT = """根据以下口述内容生成一张横版 16:9 视频背景图：

{content}

请不要直接复刻文字内容，而是提炼其中的年代感、家庭关系、地点氛围和情绪。画面要像一张旧家庭照片，带有手工上色的质感。整体为银盐胶片颗粒、轻微褪色、柔和低对比，颜色像后来用水彩淡淡补上的旧照片。使用米黄、灰褐、淡青、浅赭色调。画面真实、安静、克制，有家族相册和私人记忆的感觉。

构图上，主体不要占满画面，中央或右侧保留大面积干净留白，方便后期叠加口述文字。画面可以有生活痕迹，但不要喧宾夺主。

不要出现文字、字幕、水印、logo、现代 UI 元素。不要泛泛的 AI 插画感，不要塑料质感，不要过度光滑，不要高清数码照片感，不要强烈电影光效，不要过度锐化，不要夸张表情，不要畸形人物，不要清晰正脸特写，不要梦幻发光，不要过度饱和。"""


_INK_PAPER_PROMPT = """根据以下口述内容生成一张横版 16:9 视频背景图：

{content}

请从口述内容中提炼出场景、情绪和记忆感，生成一幅淡水墨旧纸风格的背景图。画面使用浅墨晕染、枯笔线条和少量淡彩，具有中国文人水墨画的疏淡感。整体安静、克制、含蓄，像一段记忆慢慢浮现在纸上。使用米白、淡墨、灰蓝、浅赭、灰绿色等低饱和色彩。画面要有旧纸纤维、轻微斑驳和纸本质感。

构图上强调留白，不要把场景画满。主体可以意象化、模糊化，不需要完全写实。画面中央或偏右保留干净区域，用于放置口述文字。整体像一本家庭回忆录里的章节背景，而不是旅游风景图。

不要出现文字、题字、印章、水印、logo。不要商业国风插画感，不要景区宣传图感，不要鲜艳青绿色，不要过度精致，不要强烈光影，不要照片真实感，不要动漫感，不要奇幻仙侠感，不要泛泛 AI 生成感。"""


_STORYBOOK_PROMPT = """根据以下口述内容生成一张横版 16:9 视频背景图：

{content}

请把口述内容转化为一幅二十世纪中国老连环画或旧书插图风格的画面。画面以黑白或低饱和色为主，使用细密钢笔线描、朴素写实的叙事构图，带有旧纸泛黄质感。人物和场景要有真实生活气息，不要夸张，不要戏剧化。整体像一本旧连环画、家族故事书或口述史插图中的一页。

画面应根据口述内容提炼一个关键场景，但不要堆太多元素。主体适度简化，保留大面积留白，方便叠加字幕。线条自然，有手工绘制感，低对比，安静克制。

不要出现文字、对白框、水印、logo。不要现代漫画感，不要日漫风，不要儿童绘本感，不要夸张表情，不要电影海报构图，不要过度精致，不要 3D 渲染感，不要塑料质感，不要明亮商业插画色彩，不要泛泛 AI 插画感。"""


_BACKGROUND_STYLES: tuple[ShareVideoBackgroundStyle, ...] = (
    ShareVideoBackgroundStyle(
        value="old_album",
        label="旧相册",
        description="旧照片手工上色质感，适合家庭、亲人、童年和生活片段",
        prompt_template=_OLD_ALBUM_PROMPT,
    ),
    ShareVideoBackgroundStyle(
        value="ink_paper",
        label="淡水墨",
        description="淡水墨旧纸质感，适合故乡、晚年回看和人生总结",
        prompt_template=_INK_PAPER_PROMPT,
    ),
    ShareVideoBackgroundStyle(
        value="storybook",
        label="老故事书",
        description="中国老连环画和旧书插图质感，适合具体故事和家庭事件",
        prompt_template=_STORYBOOK_PROMPT,
    ),
)
_BACKGROUND_STYLE_BY_VALUE = {style.value: style for style in _BACKGROUND_STYLES}


def share_video_background_style_options() -> list[dict[str, str]]:
    return [
        {
            "value": style.value,
            "label": style.label,
            "description": style.description,
        }
        for style in _BACKGROUND_STYLES
    ]


def normalize_share_video_background_style(value: str | None) -> str:
    normalized = (value or DEFAULT_SHARE_VIDEO_BACKGROUND_STYLE).strip()
    if normalized not in _BACKGROUND_STYLE_BY_VALUE:
        valid = "、".join(style.label for style in _BACKGROUND_STYLES)
        raise ValueError(f"未知的分享视频背景风格，可选：{valid}")
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


def build_share_video_background_prompt(selected_text: str, style_id: str | None) -> ShareVideoBackgroundPrompt:
    style = share_video_background_style(style_id)
    content = _prompt_content(selected_text)
    prompt = style.prompt_template.format(content=content).strip()
    prompt_hash = hashlib.sha256(
        "\n".join((
            SHARE_VIDEO_BACKGROUND_PROMPT_VERSION,
            style.value,
            content,
        )).encode("utf-8")
    ).hexdigest()
    return ShareVideoBackgroundPrompt(
        style=style,
        content=content,
        prompt=prompt,
        prompt_hash=prompt_hash,
        template_version=SHARE_VIDEO_BACKGROUND_PROMPT_VERSION,
    )


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
                content = await self._extract_image_content(client, result)
                if not content:
                    raise ShareVideoBackgroundGenerationError("火山图片生成返回空图片")
                usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
                return VolcArkGeneratedImage(
                    content=content,
                    mime_type=f"image/{self.output_format}",
                    model=str(result.get("model") or self.model),
                    usage=usage,
                )

        return await provider_concurrency_limiter.run(
            "ai_image",
            self.max_concurrent_requests,
            request,
        )

    async def _extract_image_content(self, client: httpx.AsyncClient, result: dict[str, object]) -> bytes:
        data = result.get("data")
        if not isinstance(data, list) or not data:
            raise ShareVideoBackgroundGenerationError("火山图片生成没有返回图片")
        first = data[0]
        if not isinstance(first, dict):
            raise ShareVideoBackgroundGenerationError("火山图片生成返回格式无效")

        b64_json = first.get("b64_json")
        if isinstance(b64_json, str) and b64_json.strip():
            return _decode_base64_image(b64_json.strip())

        url = first.get("url")
        if isinstance(url, str) and url.strip():
            try:
                response = await client.get(url.strip())
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise ShareVideoBackgroundGenerationError("火山图片下载失败") from exc
            return response.content

        raise ShareVideoBackgroundGenerationError("火山图片生成没有可用图片数据")


async def get_or_generate_share_video_background(
    selected_text: str,
    style_id: str | None,
) -> ShareVideoBackgroundAsset | None:
    settings = get_settings()
    normalized_style = normalize_share_video_background_style(style_id)
    if not settings.memoir_share_video_ai_background_enabled:
        return None
    if not settings.volc_ark_api_key.strip():
        return None

    prompt = build_share_video_background_prompt(selected_text, normalized_style)
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

    image = await VolcArkImageGenerationService().generate(prompt.prompt)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        temp_path.write_bytes(image.content)
        temp_path.replace(path)
    except OSError as exc:
        raise ShareVideoBackgroundGenerationError("分享视频背景图缓存写入失败") from exc
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
