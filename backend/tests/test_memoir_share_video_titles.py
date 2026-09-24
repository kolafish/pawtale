from types import SimpleNamespace

import pytest

from app.services.memoir import share_video_jobs
from app.services.memoir.share_video_metadata import ShareVideoJobMetadata


def test_share_video_title_uses_selected_clip_and_supports_existing_jobs():
    selected_text = "外婆在院子里给我做风筝。后来我们一起去河边放飞。"
    assert share_video_jobs.fallback_share_video_title(selected_text) == "外婆在院子里给我做风筝"
    assert share_video_jobs.share_video_title(
        ShareVideoJobMetadata.from_json({}), selected_text,
    ) == "外婆在院子里给我做风筝"
    assert share_video_jobs.share_video_download_filename("外婆/家的:风筝") == "外婆家的风筝.mp4"
    assert share_video_jobs.fallback_share_video_title("13812345678，记得那个夏天") == "回忆片段"
    assert share_video_jobs.fallback_share_video_title(
        "那天我在外婆家门口接到电话13812345678说了好久"
    ) == "回忆片段"
    assert share_video_jobs.share_video_download_filename("电话 138 1234 5678") == "回忆片段.mp4"


@pytest.mark.parametrize("model_title,expected", [
    ("院子里的风筝", "院子里的风筝"),
    ("联系我 13812345678", None),
])
async def test_share_video_ai_title_uses_only_selected_clip(
    monkeypatch, model_title, expected,
):
    selected_text = "外婆在院子里给我做风筝。"

    async def fake_generate(client, system_prompt, user_prompt, *, output_model, **kwargs):
        assert user_prompt == selected_text
        assert '{"title":"..."}' in system_prompt
        assert kwargs["max_attempts"] == 1
        return SimpleNamespace(value=output_model.model_validate({"title": model_title}))

    monkeypatch.setattr(share_video_jobs, "get_ai_service", lambda model: object())
    monkeypatch.setattr(share_video_jobs, "generate_structured_with_client", fake_generate)
    assert await share_video_jobs._suggest_share_video_title(
        selected_text, ai_model="deepseek-flash",
    ) == expected
