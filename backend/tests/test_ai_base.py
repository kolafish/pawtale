"""Tests for AI text provider contracts and use-case helpers."""
import httpx
import pytest
from pydantic import BaseModel, ConfigDict
from unittest.mock import AsyncMock, patch, MagicMock

from app.services.ai_calls import (
    generate_structured_with_client,
    generate_text_with_client,
    AIOutputTruncatedError,
)
import app.services.ai_story as ai_story
import app.services.ai_revision as ai_revision
from app.services.ai_project_analysis import normalize_project_analysis
from app.services.ai_timeouts import AIProviderTimeoutError
from app.services.ai_text import DeepSeekTextService
from app.services.ai_volc import VolcArkTextService


class _StructuredProbe(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool


class _StructuredIssueProbe(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str
    reason: str


class _StructuredListProbe(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_issues: list[_StructuredIssueProbe]


class TestBuildCharDescriptions:
    def test_full_character(self):
        chars = [{
            "name": "林晚",
            "identity": "导演",
            "personality": ["克制", "理性"],
            "speaking_style": "冷淡",
            "background": "纪录片导演",
            "core_trait": "追求真相",
            "fatal_flaw": "不信任他人",
        }]
        result = ai_story.build_char_descriptions(chars)
        assert len(result) == 1
        assert "【林晚】" in result[0]
        assert "导演" in result[0]
        assert "克制、理性" in result[0]
        assert "冷淡" in result[0]
        assert "追求真相" in result[0]
        assert "不信任他人" in result[0]

    def test_minimal_character(self):
        chars = [{"name": "小明"}]
        result = ai_story.build_char_descriptions(chars)
        assert result == ["【小明】"]

    def test_multiple_characters(self):
        chars = [{"name": "A"}, {"name": "B"}]
        assert len(ai_story.build_char_descriptions(chars)) == 2


class TestBuildSceneDescription:
    def test_basic_scene(self):
        scene = {"name": "咖啡馆", "description": "安静的角落"}
        result = ai_story.build_scene_description(scene)
        assert "场景：咖啡馆" in result
        assert "安静的角落" in result

    def test_scene_with_sensory_details(self):
        scene = {
            "name": "雨夜",
            "description": "倾盆大雨",
            "location": "街角",
            "time_weather": "深夜/暴雨",
            "sensory_details": {
                "visual": "霓虹灯倒影",
                "auditory": "雨声",
                "olfactory": "泥土气息",
            },
            "props": ["雨伞", "外套"],
        }
        result = ai_story.build_scene_description(scene)
        assert "街角" in result
        assert "霓虹灯倒影" in result
        assert "雨声" in result
        assert "泥土气息" in result
        assert "雨伞" in result


class TestGetStoryStage:
    def test_chapter_1(self):
        stage = ai_story.get_story_stage(1)
        assert stage["name"] == "开篇"

    def test_chapters_2_3(self):
        for ch in [2, 3]:
            assert ai_story.get_story_stage(ch)["name"] == "铺垫期"

    def test_chapters_4_6(self):
        for ch in [4, 5, 6]:
            assert ai_story.get_story_stage(ch)["name"] == "发展期"

    def test_chapters_7_9(self):
        for ch in [7, 8, 9]:
            assert ai_story.get_story_stage(ch)["name"] == "高潮期"

    def test_chapters_10_plus(self):
        for ch in [10, 15, 100]:
            assert ai_story.get_story_stage(ch)["name"] == "收尾期"


class TestBuildStyleGuide:
    def test_known_styles(self):
        for style in ["细腻", "白描", "诗意", "轻松"]:
            result = ai_story.build_style_guide(style, "甜")
            assert len(result) > 20

    def test_mood_additions(self):
        sweet = ai_story.build_style_guide("细腻", "甜")
        sad = ai_story.build_style_guide("细腻", "虐")
        assert "心动" in sweet
        assert "误会" in sad

    def test_unknown_style_falls_back(self):
        result = ai_story.build_style_guide("未知风格", "甜")
        assert "内心戏" in result  # falls back to 细腻


class TestParseStoryResponse:
    def test_with_title_and_choices(self):
        content = """【章节标题】
初见

【故事内容】
故事正文在这里。

【分支选项】
A. 选项一
B. 选项二"""
        result = ai_story.parse_story_response(content)
        assert result["title"] == "初见"
        assert "故事正文在这里" in result["content"]
        assert len(result["choices"]) == 2
        assert result["word_count"] > 0

    def test_without_markers(self):
        result = ai_story.parse_story_response("纯文本内容")
        assert result["content"] == "纯文本内容"
        assert result["title"] is None
        assert len(result["choices"]) == 2  # defaults

    def test_alternative_choice_marker(self):
        content = "正文\n\n【选择】\nA. 甲\nB. 乙\nC. 丙"
        result = ai_story.parse_story_response(content)
        assert len(result["choices"]) == 3


class TestNormalizeProjectAnalysis:
    def test_normalizes_settings_and_limits_items(self):
        parsed = {
            "characters": [{"name": f"角色{i}"} for i in range(12)],
            "outlines": [{"name": f"大纲{i}", "content": f"内容{i}"} for i in range(6)],
            "settings": [
                {"name": "地点", "type": "location", "content": "街角"},
                {"name": "非法类型", "type": "scene", "content": "旧类型"},
            ],
        }

        result = normalize_project_analysis(parsed)

        assert len(result["characters"]) == 10
        assert len(result["outlines"]) == 5
        assert result["settings"][0]["type"] == "location"
        assert result["settings"][1]["type"] == "note"


# ── Template Method Pattern Tests ──────────────

def _make_mock_response(content: str):
    """Helper: create an AsyncMock for _call_llm that returns content."""
    return AsyncMock(return_value=content)


def _make_mock_response_raw(content: str, raw: dict):
    """Helper: create an AsyncMock for _call_llm that handles return_raw."""
    async def mock_call(*args, **kwargs):
        if kwargs.get("return_raw"):
            return content, raw
        return content
    return mock_call


class TestTemplateMethodBothServices:
    """Verify public methods work on all text provider implementations."""

    @pytest.fixture(params=["deepseek", "volc"])
    def service(self, request):
        if request.param == "volc":
            return VolcArkTextService()
        return DeepSeekTextService()

    @pytest.mark.asyncio
    async def test_revise_chapter(self, service):
        with patch.object(service, "_call_llm", _make_mock_response("修改后的内容")):
            result = await ai_revision.revise_chapter(
                service,
                original_content="原始内容",
                revision_instruction="改得更好",
                characters=[],
            )
        assert result["content"] == "修改后的内容"

    @pytest.mark.asyncio
    async def test_revise_chapter_debug(self, service):
        with patch.object(service, "_call_llm", _make_mock_response_raw(
            "修改后的内容", {"choices": [{"message": {"content": "修改后的内容"}}]}
        )):
            result = await ai_revision.revise_chapter(
                service,
                original_content="原始内容",
                revision_instruction="改得更好",
                characters=[],
                debug=True,
            )
        assert result["content"] == "修改后的内容"
        assert "debug_info" in result
        assert result["debug_info"]["request"]["provider"] == service.provider_name

    @pytest.mark.asyncio
    async def test_generate_story(self, service):
        story_response = """【章节标题】
初见

【故事内容】
她走进了咖啡馆。

【分支选项】
A. 坐下来
B. 转身离开"""
        with patch.object(service, "_call_llm", _make_mock_response(story_response)):
            result = await ai_story.generate_story(
                service,
                characters=[{"name": "小雨", "personality": ["温柔"]}],
                scene={"name": "咖啡馆", "description": "安静"},
                mood="甜",
            )
        assert result["title"] == "初见"
        assert "咖啡馆" in result["content"]
        assert len(result["choices"]) == 2

class TestCallLlmContract:
    """Verify _call_llm implementations on both subclasses."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "service",
        [
            DeepSeekTextService(),
            VolcArkTextService(),
        ],
    )
    async def test_provider_timeout_has_stable_metadata(self, service):
        with patch("httpx.AsyncClient") as mock_client:
            inst = AsyncMock()
            inst.post = AsyncMock(side_effect=httpx.ReadTimeout("timed out"))
            inst.__aenter__ = AsyncMock(return_value=inst)
            inst.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = inst

            with pytest.raises(AIProviderTimeoutError) as error:
                await service._call_llm("sys", "user", timeout=12)

        assert error.value.provider == service.provider_name
        assert error.value.model == service.model_name
        assert error.value.timeout_seconds == 12

    @pytest.mark.asyncio
    async def test_structured_generation_retries_truncated_response(self):
        service = DeepSeekTextService()
        truncated = (
            '{"ok":',
            {
                "model": "deepseek-flash",
                "choices": [{"finish_reason": "length"}],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                },
            },
        )
        complete = (
            '{"ok": true}',
            {
                "model": "deepseek-flash",
                "choices": [{"finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": 12,
                    "completion_tokens": 3,
                    "total_tokens": 15,
                },
            },
        )
        call = AsyncMock(side_effect=[truncated, complete])

        with patch.object(service, "_call_llm", call):
            result = await generate_structured_with_client(
                service,
                "system",
                "user",
                output_model=_StructuredProbe,
                requested_model="deepseek-flash",
                temperature=0.2,
                max_tokens=512,
                timeout=30,
                max_attempts=2,
            )

        assert result.response.requested_model == "deepseek-flash"
        assert result.response.resolved_model == service.model_name
        assert result.value.ok is True
        assert result.response.attempt_count == 2
        assert result.response.usage == {
            "prompt_tokens": 22,
            "completion_tokens": 8,
            "total_tokens": 30,
        }
        assert call.await_count == 2
        assert "上次输出未通过结构校验" in call.await_args_list[1].args[1]

    @pytest.mark.asyncio
    async def test_structured_generation_retries_unescaped_quote_json(self):
        service = DeepSeekTextService()
        malformed = (
            '{"ok": "目前"过年"部分"}',
            {
                "model": "deepseek-flash",
                "choices": [{"finish_reason": "stop"}],
            },
        )
        complete = (
            '{"ok": true}',
            {
                "model": "deepseek-flash",
                "choices": [{"finish_reason": "stop"}],
            },
        )
        call = AsyncMock(side_effect=[malformed, complete])

        with patch.object(service, "_call_llm", call):
            result = await service.generate_structured(
                "system",
                "user",
                output_model=_StructuredProbe,
                requested_model="deepseek-flash",
                max_attempts=2,
            )

        assert result.value.ok is True
        assert result.response.attempt_count == 2
        assert call.await_count == 2

    @pytest.mark.asyncio
    async def test_structured_generation_reports_final_truncation(self):
        service = DeepSeekTextService()
        truncated = (
            '{"ok":',
            {
                "model": "deepseek-flash",
                "choices": [{"finish_reason": "length"}],
            },
        )

        with patch.object(
            service,
            "_call_llm",
            AsyncMock(side_effect=[truncated, truncated]),
        ):
            with pytest.raises(AIOutputTruncatedError) as error:
                await service.generate_structured(
                    "system",
                    "user",
                    output_model=_StructuredProbe,
                    requested_model="deepseek-flash",
                    max_attempts=2,
                )

        assert error.value.diagnostics["finish_reason"] == "length"
        assert error.value.diagnostics["attempt_count"] == 2

    @pytest.mark.asyncio
    async def test_deepseek_returns_string(self):
        service = DeepSeekTextService()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"choices": [{"message": {"content": "world"}}]}
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client:
            inst = AsyncMock()
            inst.post = AsyncMock(return_value=mock_resp)
            inst.__aenter__ = AsyncMock(return_value=inst)
            inst.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = inst

            result = await service._call_llm("sys", "user")
        assert isinstance(result, str)
        assert result == "world"
        _, kwargs = inst.post.call_args
        assert kwargs["json"]["model"] == "deepseek-flash"
        assert kwargs["json"]["thinking"] == {"type": "disabled"}

    @pytest.mark.asyncio
    async def test_deepseek_returns_tuple_with_raw(self):
        service = DeepSeekTextService()
        raw = {"choices": [{"message": {"content": "world"}}]}
        mock_resp = MagicMock()
        mock_resp.json.return_value = raw
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client:
            inst = AsyncMock()
            inst.post = AsyncMock(return_value=mock_resp)
            inst.__aenter__ = AsyncMock(return_value=inst)
            inst.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = inst

            result = await service._call_llm("sys", "user", return_raw=True)
        assert isinstance(result, tuple)
        assert result[0] == "world"

    @pytest.mark.asyncio
    async def test_volc_returns_string(self):
        service = VolcArkTextService()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "doubao"}],
                }
            ]
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client:
            inst = AsyncMock()
            inst.post = AsyncMock(return_value=mock_resp)
            inst.__aenter__ = AsyncMock(return_value=inst)
            inst.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = inst

            result = await service._call_llm("sys", "user")
        assert isinstance(result, str)
        assert result == "doubao"
        args, kwargs = inst.post.call_args
        assert args[0] == "https://ark.cn-beijing.volces.com/api/v3/responses"
        assert kwargs["json"]["model"] == "doubao-seed-2-0-pro-260215"
        assert kwargs["json"]["instructions"] == "sys"
        assert kwargs["json"]["input"] == [
            {"role": "user", "content": [{"type": "input_text", "text": "user"}]}
        ]
        assert kwargs["json"]["max_output_tokens"] == 4000
        assert kwargs["json"]["thinking"] == {"type": "disabled"}

    @pytest.mark.asyncio
    async def test_volc_returns_tuple_with_raw(self):
        service = VolcArkTextService()
        raw = {
            "output": [
                {
                    "type": "reasoning",
                    "summary": [{"type": "summary_text", "text": "hidden"}],
                },
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "doubao"}],
                },
            ]
        }
        mock_resp = MagicMock()
        mock_resp.json.return_value = raw
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client:
            inst = AsyncMock()
            inst.post = AsyncMock(return_value=mock_resp)
            inst.__aenter__ = AsyncMock(return_value=inst)
            inst.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = inst

            result = await service._call_llm("sys", "user", return_raw=True)
        assert isinstance(result, tuple)
        assert result[0] == "doubao"

    @pytest.mark.asyncio
    async def test_volc_structured_generation_uses_json_schema(self):
        service = VolcArkTextService()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "model": "doubao-seed-2-0-pro-260215",
            "status": "completed",
            "output": [{
                "type": "message",
                "content": [{
                    "type": "output_text",
                    "text": '{"ok": true}',
                }],
            }],
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client:
            inst = AsyncMock()
            inst.post = AsyncMock(return_value=mock_resp)
            inst.__aenter__ = AsyncMock(return_value=inst)
            inst.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = inst

            result = await service.generate_structured(
                "system",
                "user",
                output_model=_StructuredProbe,
                requested_model="doubao-seed-2-0-pro-260215",
            )

        assert result.value.ok is True
        text_format = inst.post.call_args.kwargs["json"]["text"]["format"]
        assert text_format["type"] == "json_schema"
        assert text_format["strict"] is True
        assert text_format["schema"]["properties"]["ok"]["type"] == "boolean"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("service_type", [DeepSeekTextService, VolcArkTextService])
    async def test_provider_uses_text_concurrency_limit(self, monkeypatch, service_type):
        service = service_type()
        service.max_concurrent_requests = 7
        limiter = RecordingLimiter()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"choices": [{"message": {"content": "world"}}]}
        mock_resp.raise_for_status = MagicMock()
        monkeypatch.setattr("app.services.ai_base.provider_concurrency_limiter", limiter)

        with patch("httpx.AsyncClient") as mock_client:
            inst = AsyncMock()
            inst.post = AsyncMock(return_value=mock_resp)
            inst.__aenter__ = AsyncMock(return_value=inst)
            inst.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = inst

            await service._call_llm("sys", "user")

        assert limiter.calls == [("ai_text", 7)]

    @pytest.mark.asyncio
    async def test_provider_and_model_properties(self):
        deepseek = DeepSeekTextService()
        assert deepseek.provider_name == "deepseek"
        assert deepseek.model_name == "deepseek-flash"

        volc = VolcArkTextService()
        assert volc.provider_name == "volc_ark"
        assert volc.model_name == "doubao-seed-2-0-pro-260215"


class RecordingLimiter:
    def __init__(self):
        self.calls = []

    async def run(self, key, limit, operation):
        self.calls.append((key, limit))
        return await operation()


@pytest.mark.parametrize("service_type", [VolcArkTextService])
@pytest.mark.parametrize("payload,expected", [
    (" 正文 ", "正文"),
    ([{"type": "text", "text": " 第一段 "}, {"type": "image", "url": "unused"}, "第二段", {"text": "  "}], "第一段\n第二段"),
    ({"text": " 单块正文 "}, "单块正文"),
    (None, ""),
])
def test_provider_text_content_shapes_share_normalization(service_type, payload, expected):
    assert service_type()._normalize_message_content(payload) == expected


@pytest.mark.asyncio
async def test_public_structured_call_does_not_retry_transport_timeout():
    service = DeepSeekTextService()
    call = AsyncMock(side_effect=TimeoutError("provider deadline"))
    with patch.object(service, "_call_llm", call):
        with pytest.raises(TimeoutError, match="provider deadline"):
            await generate_structured_with_client(
                service, "system", "user",
                output_model=_StructuredProbe,
                requested_model="deepseek-flash",
                temperature=0.2,
                max_tokens=512,
                timeout=30,
                max_attempts=3,
            )
    assert call.await_count == 1


@pytest.mark.asyncio
async def test_public_text_call_preserves_provider_diagnostics():
    service = DeepSeekTextService()
    raw = ("完成", {"model": service.model_name, "usage": {"total_tokens": 12}})
    with patch.object(service, "_call_llm", AsyncMock(return_value=raw)):
        result = await generate_text_with_client(
            service, "system", "user",
            requested_model="deepseek-flash",
            temperature=0.2,
            max_tokens=512,
            timeout=30,
        )
    assert result.content == "完成"
    assert result.provider == service.provider_name
    assert result.requested_model == "deepseek-flash"
    assert result.resolved_model == result.response_model == service.model_name
    assert result.usage == {"total_tokens": 12}
    assert result.attempt_count == 1
