from __future__ import annotations

import base64
from dataclasses import dataclass, field
from functools import cached_property
import hashlib
from io import BytesIO
import os
from pathlib import Path
import re
import stat
from uuid import uuid4

import httpx
from PIL import Image
from pydantic import BaseModel, model_validator
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import DATA_DIR, get_settings
from app.database import async_session_maker
from app.domain.memoir.types import MemoirImageAssetSourceType, MemoirImageAssetStatus
from app.models.memoir import MemoirImageAsset, MemoirMaterialShareVideo, MemoirShareVideoImageReceipt
from app.models.users import User
from app.services.memoir.job_leases import JobLease, JobLeaseLostError
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
    cache_lookup_path: str | None = None


class ShareVideoBackgroundCacheKey(BaseModel):
    storage_path: str
    style_id: str
    model: str
    prompt_hash: str
    template_version: str
    cache_lookup_path: str | None = None

    @model_validator(mode="before")
    @classmethod
    def read_old_scoped_key(cls, value: object) -> object:
        if isinstance(value, dict) and value.get("image_storage_path"):
            return {**value, "cache_lookup_path": value.get("storage_path"), "storage_path": value["image_storage_path"]}
        return value

    def for_slot(self, slot: ShareVideoImageSlot) -> ShareVideoBackgroundCacheKey:
        lookup = Path(self.cache_lookup_path or self.storage_path)
        cache_dir = lookup.with_name(f"{lookup.name}.images")
        slot_dir = f"{slot.lease.job_id}-{slot.name}"
        return self.model_copy(update={
            "cache_lookup_path": str(lookup),
            "storage_path": str(cache_dir / slot_dir / f"image{lookup.suffix}"),
        })

    def asset(self, *, cache_hit: bool, model: str, reusable: bool = False) -> ShareVideoBackgroundAsset:
        return ShareVideoBackgroundAsset(
            path=share_video_background_file_path(self.storage_path), storage_path=self.storage_path,
            style_id=self.style_id, prompt_hash=self.prompt_hash, prompt_template_version=self.template_version,
            provider=VolcArkImageGenerationService.provider_name, model=model, cache_hit=cache_hit,
            cache_lookup_path=(self.cache_lookup_path or self.storage_path) if reusable else None,
        )

    def cached_asset(self) -> ShareVideoBackgroundAsset | None:
        asset = self.asset(cache_hit=True, model=self.model)
        return asset if _image_file_exists(asset.path) else None


class ShareVideoReuseSource(BaseModel):
    file_path: str
    sha256: str


class ShareVideoImageReceiptKey(ShareVideoBackgroundCacheKey):
    pending_reuse: ShareVideoReuseSource | None = None


@dataclass(frozen=True)
class ShareVideoBackgroundRequest:
    prompt: ShareVideoBackgroundPrompt
    key: ShareVideoBackgroundCacheKey
    reference: ShareVideoImageReference | None


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
    content: bytes = field(repr=False)

    @cached_property
    def data_url(self) -> str:
        return "data:image/jpeg;base64," + base64.b64encode(self.content).decode("ascii")

    @cached_property
    def digest(self) -> str:
        return hashlib.sha256(self.content).hexdigest()

    @classmethod
    def from_path(cls, path: Path) -> "ShareVideoImageReference":
        try:
            with Image.open(path) as source:
                source.thumbnail((1400, 1600))
                output = BytesIO()
                source.convert("RGB").save(output, format="JPEG", quality=92)
        except (Image.DecompressionBombError, OSError) as exc:
            raise ShareVideoBackgroundGenerationError("无法读取分享视频人物参考图") from exc
        return cls(output.getvalue())

    def save(self, storage_path: str) -> None:
        _write_background(share_video_background_file_path(storage_path), self.content)

    @staticmethod
    def legacy_storage_path(digest: object) -> str | None:
        if isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest):
            return f"memoir/share-video-backgrounds/references/{digest}.jpg"
        return None

    @classmethod
    def restore(cls, digest: str, storage_path: str) -> "ShareVideoImageReference":
        try:
            reference = cls(share_video_background_file_path(storage_path).read_bytes())
        except OSError as exc:
            raise ShareVideoBackgroundGenerationError("已保存的人物参考图无法读取") from exc
        if reference.digest != digest:
            raise ShareVideoBackgroundGenerationError("已保存的人物参考图内容不匹配")
        return reference


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


def share_video_background_file_path(storage_path: str) -> Path:
    relative = Path(storage_path).relative_to(Path("memoir") / "share-video-backgrounds")
    if ".." in relative.parts:
        raise ValueError("分享视频背景图路径无效")
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


def _image_file_exists(path: Path) -> bool:
    try:
        info = path.stat()
    except FileNotFoundError:
        return False
    return stat.S_ISREG(info.st_mode) and info.st_size > 0


def _sync_background_directory(path: Path) -> None:
    dir_fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def _background_file_digest(path: Path) -> str | None:
    try:
        with path.open("rb") as image:
            return hashlib.file_digest(image, "sha256").hexdigest()
    except FileNotFoundError:
        return None


async def _reuse_background(
    db: AsyncSession, key: ShareVideoBackgroundCacheKey, slot: ShareVideoImageSlot,
) -> ShareVideoBackgroundAsset | None:
    cached = key.cached_asset()
    lookup = key.cache_lookup_path
    if lookup is None:
        return cached

    receipt = MemoirShareVideoImageReceipt
    legacy_path = receipt.cache_key["storage_path"].as_string()
    scoped_lookup = receipt.cache_key["cache_lookup_path"].as_string()
    old_image_path = receipt.cache_key["image_storage_path"].as_string()
    stored_image_path = func.coalesce(old_image_path, legacy_path)
    # Older workers could leave unsettled bytes at the shared legacy path.
    legacy_unsettled = (
        select(receipt.job_id).where(
            legacy_path == lookup, scoped_lookup.is_(None), old_image_path.is_(None),
            receipt.pending_content.is_not(None),
        ).exists().correlate(None)
    )
    settled = select(stored_image_path, receipt.model).where(
        func.coalesce(scoped_lookup, legacy_path) == lookup,
        receipt.pending_content.is_(None),
        receipt.cache_key["pending_reuse"].as_string().is_(None),
        or_(stored_image_path != lookup, ~legacy_unsettled),
    )
    # The image library can keep a confirmed image after its video and receipt are deleted.
    registered = select(MemoirImageAsset.storage_path, MemoirImageAsset.model).where(
        MemoirImageAsset.prompt_hash == key.prompt_hash,
        MemoirImageAsset.source_type == MemoirImageAssetSourceType.SHARE_VIDEO_BACKGROUND.value,
        MemoirImageAsset.status == MemoirImageAssetStatus.READY.value,
        or_(
            and_(MemoirImageAsset.storage_path == lookup, ~legacy_unsettled),
            and_(
                MemoirImageAsset.storage_path != lookup,
                MemoirImageAsset.metadata_json["share_video_image_cache_lookup_path"].as_string() == lookup,
            ),
        ),
    )
    for storage_path, model in await db.execute(settled.union(registered)):
        source = share_video_background_file_path(storage_path)
        if not _image_file_exists(source):
            continue
        if cached:
            # Old workers may have left an unregistered link. Verify its source
            # rather than certifying every file left by an older worker.
            try:
                if not cached.path.samefile(source):
                    continue
            except FileNotFoundError:
                continue
            _sync_background_directory(cached.path.parent)
            db.add(receipt(
                job_id=slot.lease.job_id, slot=slot.name, model=model, cache_key=key.model_dump(),
            ))
            return key.asset(cache_hit=True, model=model, reusable=True)

        digest = _background_file_digest(source)
        if digest is None:
            continue
        reservation = ShareVideoImageReceiptKey(
            **key.model_dump(), pending_reuse=ShareVideoReuseSource(file_path=storage_path, sha256=digest),
        )
        identity = (slot.lease.job_id, slot.name)
        db.add(receipt(
            job_id=slot.lease.job_id, slot=slot.name, model=model, cache_key=reservation.model_dump(),
        ))
        # Commit evidence of a confirmed source before creating a link. Recovery
        # can verify the destination even if that source has since been deleted.
        await db.commit()
        await lock_share_video_image_owner(db, slot.lease, user_id=slot.usage.user_id)
        reserved = await db.get(receipt, identity, populate_existing=True)
        if reserved is None:
            return None
        asset = await _recover_image_receipt(db, key, slot, reserved, cache_hit=True)
        if asset is not None:
            return asset
        # The reserved source disappeared. Other confirmed copies can still be used.
        await db.flush()
    return cached


async def _finish_background_reuse(
    db: AsyncSession, receipt: MemoirShareVideoImageReceipt, key: ShareVideoImageReceiptKey,
    source: ShareVideoReuseSource, asset: ShareVideoBackgroundAsset,
) -> ShareVideoBackgroundAsset | None:
    if not asset.path.exists():
        original = share_video_background_file_path(source.file_path)
        asset.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            asset.path.hardlink_to(original)
        except FileNotFoundError:
            # Nothing was copied, so a later provider request may fill the slot.
            await db.delete(receipt)
            return None
    if _background_file_digest(asset.path) != source.sha256:
        asset.path.unlink(missing_ok=True)
        await db.delete(receipt)
        return None
    _sync_background_directory(asset.path.parent)
    receipt.cache_key = key.model_dump(exclude={"pending_reuse"})
    return asset


def _write_background(path: Path, content: bytes, *, overwrite: bool = True) -> bool:
    temp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with temp_path.open("xb") as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            installed = True
            if overwrite:
                temp_path.replace(path)
            else:
                try:
                    path.hardlink_to(temp_path)
                except FileExistsError:
                    installed = False
            _sync_background_directory(path.parent)
            return installed
        finally:
            temp_path.unlink(missing_ok=True)
    except OSError as exc:
        raise ShareVideoBackgroundGenerationError("分享视频背景图缓存写入失败") from exc


def prepare_share_video_image_request(
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
    slot: ShareVideoImageSlot | None = None,
) -> ShareVideoBackgroundRequest:
    settings = get_settings()
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
    key = ShareVideoBackgroundCacheKey(
        storage_path=storage_path, style_id=prompt.style.value, model=model,
        prompt_hash=prompt.prompt_hash, template_version=prompt.template_version,
    )
    return ShareVideoBackgroundRequest(prompt, key.for_slot(slot) if slot else key, reference)


async def lock_share_video_image_owner(db: AsyncSession, lease: JobLease, *, user_id: str | None) -> None:
    # Account deletion locks the user before the job. Asset registration and
    # billing both take foreign-key locks on the user in the same transaction.
    if user_id:
        await db.scalar(select(User.id).where(User.id == user_id).with_for_update(read=True, key_share=True))
    owner = await db.scalar(
        select(MemoirMaterialShareVideo.id).where(
            MemoirMaterialShareVideo.id == lease.job_id, MemoirMaterialShareVideo.lease_token == lease.token,
        ).with_for_update()
    )
    if owner is None:
        raise JobLeaseLostError("视频任务已结束，无法保存配图")


@dataclass(frozen=True)
class ShareVideoImageSlot:
    lease: JobLease
    name: str
    usage: AIUsageContext

    async def stage(self, key: ShareVideoBackgroundCacheKey, image: VolcArkGeneratedImage) -> bool:
        async with async_session_maker.begin() as db:
            await lock_share_video_image_owner(db, self.lease, user_id=self.usage.user_id)
            if await db.get(MemoirShareVideoImageReceipt, (self.lease.job_id, self.name)) is not None:
                return False
            if key.cached_asset():
                # Another delivery of this slot has already acquired a free image.
                return False
            db.add(MemoirShareVideoImageReceipt(
                job_id=self.lease.job_id, slot=self.name, model=image.model,
                cache_key=key.model_dump(), pending_content=image.content,
            ))
            return True


async def _recover_image_receipt(
    db: AsyncSession, key: ShareVideoBackgroundCacheKey, slot: ShareVideoImageSlot,
    receipt: MemoirShareVideoImageReceipt, *, cache_hit: bool,
) -> ShareVideoBackgroundAsset | None:
    receipt_key = ShareVideoImageReceiptKey.model_validate(receipt.cache_key)
    if receipt_key.pending_reuse is not None:
        asset = receipt_key.asset(cache_hit=True, model=receipt.model, reusable=True)
        return await _finish_background_reuse(db, receipt, receipt_key, receipt_key.pending_reuse, asset)
    original: ShareVideoBackgroundCacheKey = receipt_key
    if receipt.pending_content is not None and original.cache_lookup_path is None:
        # Older, unsettled receipts still point to the shared file. The worker
        # checkpoints a new slot path before resuming them.
        if key.cache_lookup_path != original.storage_path:
            raise ShareVideoBackgroundGenerationError("暂存背景图的保存路径尚未登记")
        original = key
        receipt.cache_key = original.model_dump()
    asset = original.asset(cache_hit=cache_hit, model=receipt.model, reusable=True)
    if receipt.pending_content is None:
        if not _image_file_exists(asset.path):
            raise ShareVideoBackgroundGenerationError("已生成的背景图文件缺失")
        return asset
    try:
        if not _write_background(asset.path, receipt.pending_content, overwrite=False):
            if asset.path.read_bytes() != receipt.pending_content:
                raise ShareVideoBackgroundGenerationError("已保存的背景图与暂存内容不匹配")
    except ShareVideoBackgroundGenerationError as exc:
        # Retry the job against the staged bytes, even for a single-frame video.
        raise OSError("暂存的分享视频背景图尚未写入缓存") from exc
    await record_ai_usage(
        provider=asset.provider, model=asset.model, status="succeeded", image_count=1,
        cost=estimate_image_cost(provider=asset.provider, model=asset.model, image_count=1),
        context=slot.usage, db=db,
    )
    # Clearing the bytes and debiting share one commit under the job lock.
    receipt.pending_content = None
    return asset


async def recover_share_video_background(
    key: ShareVideoBackgroundCacheKey | None, slot: ShareVideoImageSlot | None, *, cache_hit: bool = True,
) -> ShareVideoBackgroundAsset | None:
    if key is None:
        return None
    if slot is None:
        return key.cached_asset()
    async with async_session_maker() as db:
        await lock_share_video_image_owner(db, slot.lease, user_id=slot.usage.user_id)
        receipt = await db.get(MemoirShareVideoImageReceipt, (slot.lease.job_id, slot.name))
        if receipt is None:
            asset = await _reuse_background(db, key, slot)
        else:
            asset = await _recover_image_receipt(db, key, slot, receipt, cache_hit=cache_hit)
        await db.commit()
        return asset


async def get_or_generate_share_video_background(
    request: ShareVideoBackgroundRequest, *, slot: ShareVideoImageSlot | None = None,
    cache_only: bool = False,
) -> ShareVideoBackgroundAsset | None:
    if cached := await recover_share_video_background(request.key, slot):
        return cached
    settings = get_settings()
    if cache_only or not settings.memoir_share_video_ai_background_enabled or not settings.volc_ark_api_key.strip():
        return None
    model = request.key.model
    try:
        if slot and slot.usage.user_id:
            await assert_ai_credit_available(slot.usage.user_id)
        image = await VolcArkImageGenerationService(model=model).generate(
            request.prompt.prompt, reference_url=request.reference.data_url if request.reference else None,
        )
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
            context=slot.usage if slot else None,
        )
        raise
    if slot:
        staged = await slot.stage(request.key, image)
        return await recover_share_video_background(request.key, slot, cache_hit=not staged)
    asset = request.key.asset(cache_hit=False, model=image.model)
    if _write_background(asset.path, image.content, overwrite=False):
        return asset
    return request.key.cached_asset()
