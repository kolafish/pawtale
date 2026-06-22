from app.services.ai_debug import build_debug_info
from app.services.ai_story import parse_story_response


class TestAITextService:
    """AI 文本服务测试"""

    def test_parse_story_response_with_choices(self):
        """测试解析带分支选项的故事响应"""
        content = """【故事内容】
你在咖啡馆看到他坐在角落，阳光洒在他身上。

【分支选项】
A. 走过去打招呼
B. 假装没看到
C. 找个位置偷偷观察"""

        result = parse_story_response(content)

        assert "咖啡馆" in result["content"]
        assert len(result["choices"]) == 3
        assert "A. 走过去打招呼" in result["choices"][0]["label"]

    def test_parse_story_response_without_choices(self):
        """测试解析没有分支选项的故事响应"""
        content = "这是一段没有分支选项的故事内容。"

        result = parse_story_response(content)

        assert result["content"] == content
        assert len(result["choices"]) == 2

    def test_parse_story_response_alternative_format(self):
        """测试解析替代格式的分支选项"""
        content = """故事内容在这里。

【选择】
A. 选项一
B. 选项二"""

        result = parse_story_response(content)

        assert len(result["choices"]) == 2


class TestAIDebugHelpers:
    def test_build_debug_info_masks_sensitive_and_truncates(self):
        debug_info = build_debug_info(
            request_info={
                "provider": "deepseek",
                "model": "deepseek-v4-flash",
                "authorization": "Bearer very-secret-token",
                "max_tokens": 3000,
                "nested": {"api_key": "abc123456789"},
            },
            response_text="x" * 9000,
            response_json={"token": "resp-secret-token"},
        )

        assert debug_info["request"]["authorization"] != "Bearer very-secret-token"
        assert "***" in debug_info["request"]["authorization"]
        assert debug_info["request"]["max_tokens"] == 3000
        assert debug_info["request"]["nested"]["api_key"] != "abc123456789"
        assert "***" in debug_info["response"]["raw_json"]["token"]
        assert debug_info["meta"]["truncated"] is True
