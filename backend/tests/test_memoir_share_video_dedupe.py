from app.services.memoir.share_video_jobs import _share_video_options_signature
from app.services.memoir.share_video_metadata import ShareVideoJobMetadata


def test_share_video_request_signature_changes_for_expensive_options():
    metadata = ShareVideoJobMetadata(
        ai_model="deepseek-flash",
        background_source="ai",
        background_style="warm",
        background_mode="smart_multi",
        background_effective_mode="smart_multi",
    )
    assert _share_video_options_signature(metadata) == _share_video_options_signature(metadata)
    assert _share_video_options_signature(
        metadata.model_copy(update={"background_style": "cool"})
    ) != _share_video_options_signature(metadata)
