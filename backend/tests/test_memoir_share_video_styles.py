from io import BytesIO
from types import SimpleNamespace

from PIL import Image

from app.models.memoir import MemoirMaterialShareVideo
from app.models.projects import Project
from app.services.memoir import share_video_alignment, share_video_jobs
from app.services.memoir import image_assets
from app.services.memoir.project_ai_preferences import (
    project_share_video_style,
    remember_project_share_video_style,
)
from app.services.memoir.share_video_metadata import ShareVideoJobMetadata
from tests.memoir_helpers import create_test_voice_transcript


def test_legacy_style_and_current_render_signatures():
    project = Project(metadata_json={})
    assert project_share_video_style(project) == "illustrated"
    remember_project_share_video_style(project, "uploaded")
    assert project_share_video_style(project) == "uploaded"
    assert ShareVideoJobMetadata.from_json({}).video_style == "illustrated"
    uploaded = ShareVideoJobMetadata.from_json({"background_source": "custom", "custom_background_asset_id": "photo"})
    assert uploaded.video_style == "uploaded"
    assert uploaded.photo_asset_ids == ["photo"]
    illustrated = ShareVideoJobMetadata(ai_model="deepseek-flash", background_source="ai")
    signature = share_video_jobs._share_video_options_signature
    assert signature(uploaded) != signature(illustrated)


def _settings(*, ai_enabled=False):
    return SimpleNamespace(
        volc_ark_image_model="doubao-seedream-5-0-lite-260128",
        memoir_share_video_ai_background_enabled=ai_enabled,
        volc_ark_api_key="test-key" if ai_enabled else "",
        memoir_share_video_lease_seconds=60,
        memoir_share_video_max_attempts=3,
        memoir_share_video_retry_seconds=1,
    )


async def test_project_style_default_override_and_task_snapshot(client, test_db, monkeypatch):
    settings = _settings(ai_enabled=True)
    monkeypatch.setattr(share_video_jobs, "get_settings", lambda: settings)
    monkeypatch.setattr(share_video_alignment, "get_settings", lambda: settings)
    created_project = await client.post("/api/mobile/memoir/projects", json={
        "title": "父亲的往事", "share_video_style": "illustrated",
    })
    assert created_project.status_code == 201
    project_id = created_project.json()["id"]
    assert created_project.json()["metadata_json"]["share_video_style"] == "illustrated"
    text = "后来我们约起一起走。"
    created = await create_test_voice_transcript(client, test_db, text=text, project_id=project_id)
    base = f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}/share-video"
    context = (await client.get(f"{base}/context")).json()
    assert context["default_video_style"] == "illustrated"
    assert [option["value"] for option in context["video_styles"]] == ["uploaded", "illustrated"]
    assert context["image_unit_price_rmb"] == "0.33"
    assert "约 0.33 元/张，一条最多 5 张" in context["video_styles"][1]["description"]
    selection = {"start_char": 0, "end_char": len(text)}
    created_job = await client.post(f"{base}/jobs", json=selection)
    assert created_job.status_code == 201, created_job.text
    assert created_job.json()["video_style"] == "illustrated"
    repeated = await client.post(f"{base}/jobs", json={**selection, "video_style": "illustrated"})
    assert repeated.json()["id"] == created_job.json()["id"]
    updated = await client.patch(f"/api/mobile/memoir/projects/{project_id}", json={"share_video_style": "uploaded"})
    assert updated.status_code == 200
    assert updated.json()["metadata_json"]["share_video_style"] == "uploaded"
    detail = await client.get(f"/api/mobile/memoir/share-video/jobs/{created_job.json()['id']}")
    assert detail.json()["video_style"] == "illustrated"
    assert (await client.get(f"{base}/context")).json()["default_video_style"] == "uploaded"


async def test_explicit_styles_require_their_source_but_legacy_default_survives(client, test_db, monkeypatch):
    monkeypatch.setattr(share_video_jobs, "get_settings", lambda: _settings())
    text = "后来我们约起一起走。"
    created = await create_test_voice_transcript(client, test_db, text=text)
    url = f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}/share-video/jobs"
    selection = {"start_char": 0, "end_char": len(text)}
    unavailable = await client.post(url, json={**selection, "video_style": "illustrated"})
    assert unavailable.status_code == 400
    assert "AI配图" in unavailable.json()["detail"]
    upload = await client.post(url, json={**selection, "video_style": "uploaded"})
    assert upload.status_code == 400
    assert upload.json()["detail"] == "请选择照片"
    project = await test_db.get(Project, created["session"]["project_id"])
    project.metadata_json = {key: value for key, value in project.metadata_json.items() if key != "share_video_style"}
    await test_db.commit()
    legacy = await client.post(url, json=selection)
    assert legacy.status_code == 201
    assert legacy.json()["background_source"] == "classic"
    # Retired stored styles can still be read, but must not fulfill a new request.
    job = await test_db.get(MemoirMaterialShareVideo, legacy.json()["id"])
    job.metadata_json = {**job.metadata_json, "video_style": "retired"}
    await test_db.commit()
    detail = await client.get(f"/api/mobile/memoir/share-video/jobs/{job.id}")
    assert detail.status_code == 200
    rejected = await client.post(url, json={**selection, "video_style": "retired"})
    assert rejected.status_code == 422
    replacement = await client.post(url, json=selection)
    assert replacement.status_code == 201
    assert replacement.json()["id"] != job.id


async def test_uploaded_style_keeps_asset_identity_and_separate_ai_jobs(client, test_db, tmp_path, monkeypatch):
    monkeypatch.setattr(share_video_jobs, "get_settings", lambda: _settings(ai_enabled=True))
    monkeypatch.setattr(image_assets, "DATA_DIR", tmp_path)
    text = "后来我们约起一起走。"
    created = await create_test_voice_transcript(client, test_db, text=text)
    project_id = created["session"]["project_id"]
    asset_ids = []
    for color in ("red", "blue"):
        source = BytesIO()
        Image.new("RGB", (8, 8), color).save(source, format="PNG")
        uploaded = await client.post(
            f"/api/mobile/memoir/projects/{project_id}/image-assets",
            files={"file": ("picture.png", source.getvalue(), "image/png")},
        )
        assert uploaded.status_code == 201, uploaded.text
        asset_ids.append(uploaded.json()["id"])
    url = f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}/share-video/jobs"
    selection = {"start_char": 0, "end_char": len(text)}
    jobs = []
    for asset_id in asset_ids:
        response = await client.post(url, json={
            **selection, "video_style": "uploaded", "photo_asset_ids": [asset_id],
        })
        assert response.status_code == 201, response.text
        assert response.json()["video_style"] == "uploaded"
        assert response.json()["background_source"] == "custom"
        jobs.append(response.json()["id"])
    repeated = await client.post(url, json={
        **selection, "video_style": "uploaded", "photo_asset_ids": [asset_ids[0]],
    })
    assert repeated.json()["id"] == jobs[0]
    illustrated = await client.post(url, json={**selection, "video_style": "illustrated"})
    assert illustrated.status_code == 201
    assert len(set([*jobs, illustrated.json()["id"]])) == 3
