"""Tests for AITextServiceBase shared helpers and template method pattern."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from app.services.ai_base import AITextServiceBase
from app.services.ai_text import DeepSeekTextService
from app.services.ai_claude import ClaudeTextService


# ── Helper Unit Tests ──────────────────────────


class TestExtractJson:
    def test_fenced_json_block(self):
        content = '```json\n{"key": "value"}\n```'
        assert AITextServiceBase._extract_json(content) == '{"key": "value"}'

    def test_fenced_block_without_json_label(self):
        content = '```\n{"key": "value"}\n```'
        assert AITextServiceBase._extract_json(content) == '{"key": "value"}'

    def test_plain_json(self):
        content = '{"key": "value"}'
        assert AITextServiceBase._extract_json(content) == '{"key": "value"}'

    def test_text_around_json(self):
        content = '这是说明\n```json\n{"k": 1}\n```\n后续文字'
        assert AITextServiceBase._extract_json(content) == '{"k": 1}'


class TestExtractJsonPayload:
    def test_fenced_json(self):
        content = '```json\n{"a": 1}\n```'
        assert AITextServiceBase._extract_json_payload(content) == {"a": 1}

    def test_plain_json(self):
        assert AITextServiceBase._extract_json_payload('{"a": 1}') == {"a": 1}

    def test_json_with_surrounding_text(self):
        content = '以下是结果：\n{"a": 1}\n以上'
        assert AITextServiceBase._extract_json_payload(content) == {"a": 1}

    def test_empty_string(self):
        assert AITextServiceBase._extract_json_payload("") == {}

    def test_invalid_json(self):
        assert AITextServiceBase._extract_json_payload("not json") == {}

    def test_returns_dict_only(self):
        assert AITextServiceBase._extract_json_payload("[1, 2, 3]") == {}


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
        result = AITextServiceBase._build_char_descriptions(chars)
        assert len(result) == 1
        assert "【林晚】" in result[0]
        assert "导演" in result[0]
        assert "克制、理性" in result[0]
        assert "冷淡" in result[0]
        assert "追求真相" in result[0]
        assert "不信任他人" in result[0]

    def test_minimal_character(self):
        chars = [{"name": "小明"}]
        result = AITextServiceBase._build_char_descriptions(chars)
        assert result == ["【小明】"]

    def test_multiple_characters(self):
        chars = [{"name": "A"}, {"name": "B"}]
        assert len(AITextServiceBase._build_char_descriptions(chars)) == 2


class TestBuildSceneDescription:
    def test_basic_scene(self):
        scene = {"name": "咖啡馆", "description": "安静的角落"}
        result = AITextServiceBase._build_scene_description(scene)
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
        result = AITextServiceBase._build_scene_description(scene)
        assert "街角" in result
        assert "霓虹灯倒影" in result
        assert "雨声" in result
        assert "泥土气息" in result
        assert "雨伞" in result


class TestGetStoryStage:
    def test_chapter_1(self):
        stage = AITextServiceBase._get_story_stage(1)
        assert stage["name"] == "开篇"

    def test_chapters_2_3(self):
        for ch in [2, 3]:
            assert AITextServiceBase._get_story_stage(ch)["name"] == "铺垫期"

    def test_chapters_4_6(self):
        for ch in [4, 5, 6]:
            assert AITextServiceBase._get_story_stage(ch)["name"] == "发展期"

    def test_chapters_7_9(self):
        for ch in [7, 8, 9]:
            assert AITextServiceBase._get_story_stage(ch)["name"] == "高潮期"

    def test_chapters_10_plus(self):
        for ch in [10, 15, 100]:
            assert AITextServiceBase._get_story_stage(ch)["name"] == "收尾期"


class TestBuildStyleGuide:
    def test_known_styles(self):
        stage = AITextServiceBase._get_story_stage(1)
        for style in ["细腻", "白描", "诗意", "轻松"]:
            result = AITextServiceBase._build_style_guide(style, "甜", stage)
            assert len(result) > 20

    def test_mood_additions(self):
        stage = AITextServiceBase._get_story_stage(1)
        sweet = AITextServiceBase._build_style_guide("细腻", "甜", stage)
        sad = AITextServiceBase._build_style_guide("细腻", "虐", stage)
        assert "心动" in sweet
        assert "误会" in sad

    def test_unknown_style_falls_back(self):
        stage = AITextServiceBase._get_story_stage(1)
        result = AITextServiceBase._build_style_guide("未知风格", "甜", stage)
        assert "内心戏" in result  # falls back to 细腻


class TestParseStoryResponse:
    def setup_method(self):
        self.service = DeepSeekTextService()

    def test_with_title_and_choices(self):
        content = """【章节标题】
初见

【故事内容】
故事正文在这里。

【分支选项】
A. 选项一
B. 选项二"""
        result = self.service._parse_story_response(content)
        assert result["title"] == "初见"
        assert "故事正文在这里" in result["content"]
        assert len(result["choices"]) == 2
        assert result["word_count"] > 0

    def test_without_markers(self):
        result = self.service._parse_story_response("纯文本内容")
        assert result["content"] == "纯文本内容"
        assert result["title"] is None
        assert len(result["choices"]) == 2  # defaults

    def test_alternative_choice_marker(self):
        content = "正文\n\n【选择】\nA. 甲\nB. 乙\nC. 丙"
        result = self.service._parse_story_response(content)
        assert len(result["choices"]) == 3


class TestNormalizeExtractedContext:
    def test_valid_data(self):
        data = {
            "characters": [
                {"name": "林晚", "role_hint": "主角", "traits": ["克制"], "summary": "导演"},
            ],
            "scenes": [
                {"name": "天台", "description": "夜风", "keywords": ["重逢"]},
            ],
            "outline_points": [
                {"stage": "开端", "summary": "重逢"},
            ],
            "consistency_notes": ["注意时间线"],
        }
        result = AITextServiceBase._normalize_extracted_context(data)
        assert len(result["extracted_characters"]) == 1
        assert result["extracted_characters"][0]["name"] == "林晚"
        assert len(result["extracted_scenes"]) == 1
        assert len(result["extracted_outline_points"]) == 1
        assert result["extraction_notes"] == ["注意时间线"]

    def test_empty_data(self):
        result = AITextServiceBase._normalize_extracted_context({})
        assert result["extracted_characters"] == []
        assert result["extracted_scenes"] == []
        assert result["extracted_outline_points"] == []
        assert result["extraction_notes"] == []

    def test_filters_invalid_entries(self):
        data = {
            "characters": [
                {"name": "", "traits": []},  # empty name — should be skipped
                {"name": "有效", "traits": ["a"]},
                "not a dict",  # invalid type — should be skipped
            ],
            "scenes": [{"name": "", "description": "x"}],  # empty name
            "outline_points": [{"stage": "", "summary": ""}],  # empty both
        }
        result = AITextServiceBase._normalize_extracted_context(data)
        assert len(result["extracted_characters"]) == 1
        assert result["extracted_characters"][0]["name"] == "有效"
        assert result["extracted_scenes"] == []
        assert result["extracted_outline_points"] == []

    def test_limits_to_8(self):
        data = {
            "characters": [{"name": f"角色{i}", "traits": []} for i in range(20)],
        }
        result = AITextServiceBase._normalize_extracted_context(data)
        assert len(result["extracted_characters"]) == 8


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
    """Verify public methods work on both ClaudeTextService and DeepSeekTextService."""

    @pytest.fixture(params=["claude", "deepseek"])
    def service(self, request):
        if request.param == "claude":
            return ClaudeTextService()
        return DeepSeekTextService()

    @pytest.mark.asyncio
    async def test_auto_complete_character(self, service):
        with patch.object(service, "_call_llm", _make_mock_response(
            '{"gender": "女", "age": 22, "identity": "画家", "appearance": "短发", '
            '"personality": ["温柔"], "speaking_style": "轻声", "background": "艺术生"}'
        )):
            result = await service.auto_complete_character(name="小雨")
        assert result["gender"] == "女"
        assert result["age"] == 22

    @pytest.mark.asyncio
    async def test_generate_chapter_title(self, service):
        with patch.object(service, "_call_llm", _make_mock_response("月下重逢")):
            result = await service.generate_chapter_title("两人在月光下重逢的故事")
        assert result == "月下重逢"

    @pytest.mark.asyncio
    async def test_revise_chapter(self, service):
        with patch.object(service, "_call_llm", _make_mock_response("修改后的内容")):
            result = await service.revise_chapter(
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
            result = await service.revise_chapter(
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
            result = await service.generate_story(
                characters=[{"name": "小雨", "personality": ["温柔"]}],
                scene={"name": "咖啡馆", "description": "安静"},
                mood="甜",
            )
        assert result["title"] == "初见"
        assert "咖啡馆" in result["content"]
        assert len(result["choices"]) == 2

    @pytest.mark.asyncio
    async def test_extract_story_context(self, service):
        json_response = '{"characters": [{"name": "A", "role_hint": "主角", "traits": ["勇敢"], "summary": "英雄"}], "scenes": [], "outline_points": [], "consistency_notes": []}'
        with patch.object(service, "_call_llm", _make_mock_response(json_response)):
            result = await service.extract_story_context("正文内容")
        assert result["extracted_characters"][0]["name"] == "A"

    @pytest.mark.asyncio
    async def test_enhance_outline(self, service):
        with patch.object(service, "_call_llm", _make_mock_response(
            '{"central_conflict": "冲突", "plot_hints": ["提示"], "suggested_mood": "甜", "suggested_ending": "HE"}'
        )):
            result = await service.enhance_outline(title="测试故事")
        assert result["central_conflict"] == "冲突"

    @pytest.mark.asyncio
    async def test_generate_outline_draft(self, service):
        with patch.object(service, "_call_llm", _make_mock_response(
            '{"title": "T", "mood": "甜", "writing_style": "细腻", "central_conflict": "C", "ending_type": "HE", "plot_hints": ["a"], "forbidden_elements": []}'
        )):
            result = await service.generate_outline_draft(
                story_goal="目标", character_tension="张力", central_conflict="冲突",
            )
        assert result["title"] == "T"

    @pytest.mark.asyncio
    async def test_regenerate_choices(self, service):
        with patch.object(service, "_call_llm", _make_mock_response(
            '{"choices": [{"label": "A. 继续"}, {"label": "B. 转折"}]}'
        )):
            result = await service.regenerate_choices(
                current_content="故事内容",
                characters=[{"name": "角色", "personality": ["温柔"]}],
            )
        assert len(result) == 2
        assert "A. 继续" in result[0]["label"]


class TestCallLlmContract:
    """Verify _call_llm implementations on both subclasses."""

    @pytest.mark.asyncio
    async def test_claude_returns_string(self):
        service = ClaudeTextService()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"choices": [{"message": {"content": "hello"}}]}
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client:
            inst = AsyncMock()
            inst.post = AsyncMock(return_value=mock_resp)
            inst.__aenter__ = AsyncMock(return_value=inst)
            inst.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = inst

            result = await service._call_llm("sys", "user")
        assert isinstance(result, str)
        assert result == "hello"

    @pytest.mark.asyncio
    async def test_claude_returns_tuple_with_raw(self):
        service = ClaudeTextService()
        raw = {"choices": [{"message": {"content": "hello"}}]}
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
        assert result[0] == "hello"
        assert isinstance(result[1], dict)

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
    async def test_provider_and_model_properties(self):
        claude = ClaudeTextService()
        assert claude.provider_name == "claude"
        assert "claude" in claude.model_name

        deepseek = DeepSeekTextService()
        assert deepseek.provider_name == "deepseek"
        assert deepseek.model_name == "deepseek-chat"
