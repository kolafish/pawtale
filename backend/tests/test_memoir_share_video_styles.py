from hashlib import sha256
from io import BytesIO
from types import SimpleNamespace

from PIL import Image
import pytest

from app.models.memoir import MemoirMaterialShareVideo
from app.models.projects import Project
from app.services.memoir import share_video_alignment, share_video_jobs
from app.services.memoir import image_assets, share_video_custom_backgrounds
from app.services.memoir.project_ai_preferences import (
    project_share_video_style,
    remember_project_share_video_style,
)
from app.services.memoir.share_video_metadata import ShareVideoJobMetadata
from app.services.memoir.share_video_story_plan import (
    SHARE_VIDEO_STORY_PLAN_VERSION,
    ShareVideoStoryPlan,
    ShareVideoStoryPlanningError,
)
from tests.memoir_helpers import create_test_voice_transcript


def test_legacy_style_and_three_expensive_render_signatures():
    project = Project(metadata_json={})
    assert project_share_video_style(project) == "illustrated"
    remember_project_share_video_style(project, "story_animation")
    assert project_share_video_style(project) == "story_animation"
    assert ShareVideoJobMetadata.from_json({}).video_style == "illustrated"
    uploaded = ShareVideoJobMetadata.from_json({"background_source": "custom"})
    assert uploaded.video_style == "uploaded"
    illustrated = ShareVideoJobMetadata(ai_model="deepseek-flash", background_source="ai")
    animated = illustrated.model_copy(update={
        "video_style": "story_animation", "animation_version": SHARE_VIDEO_STORY_PLAN_VERSION,
    })
    signature = share_video_jobs._share_video_options_signature
    assert len({signature(uploaded), signature(illustrated), signature(animated)}) == 3
    assert signature(animated) != signature(animated.model_copy(update={"animation_version": "next"}))
    assert signature(animated) == signature(animated.model_copy(update={"background_style": "pencil"}))


def _settings(*, ai_enabled=False):
    return SimpleNamespace(
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
        "title": "父亲的往事", "share_video_style": "story_animation",
    })
    assert created_project.status_code == 201
    project_id = created_project.json()["id"]
    assert created_project.json()["metadata_json"]["share_video_style"] == "story_animation"
    text = "后来我们约起一起走。"
    created = await create_test_voice_transcript(client, test_db, text=text, project_id=project_id)
    base = f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}/share-video"
    context = (await client.get(f"{base}/context")).json()
    assert context["default_video_style"] == "story_animation"
    assert [option["value"] for option in context["video_styles"]] == ["uploaded", "illustrated", "story_animation"]
    selection = {"start_char": 0, "end_char": len(text)}
    animated = await client.post(f"{base}/jobs", json=selection)
    assert animated.status_code == 201, animated.text
    assert animated.json()["video_style"] == "story_animation"
    repeated = await client.post(f"{base}/jobs", json={**selection, "video_style": "story_animation"})
    assert repeated.json()["id"] == animated.json()["id"]
    illustrated = await client.post(f"{base}/jobs", json={**selection, "video_style": "illustrated"})
    assert illustrated.status_code == 201
    assert illustrated.json()["id"] != animated.json()["id"]
    assert illustrated.json()["background_source"] == "ai"
    updated = await client.patch(f"/api/mobile/memoir/projects/{project_id}", json={"share_video_style": "uploaded"})
    assert updated.status_code == 200
    assert updated.json()["metadata_json"]["share_video_style"] == "uploaded"
    detail = await client.get(f"/api/mobile/memoir/share-video/jobs/{animated.json()['id']}")
    assert detail.json()["video_style"] == "story_animation"
    assert (await client.get(f"{base}/context")).json()["default_video_style"] == "uploaded"


async def test_explicit_styles_require_their_source_but_legacy_default_survives(client, test_db, monkeypatch):
    monkeypatch.setattr(share_video_jobs, "get_settings", lambda: _settings())
    text = "后来我们约起一起走。"
    created = await create_test_voice_transcript(client, test_db, text=text)
    url = f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}/share-video/jobs"
    selection = {"start_char": 0, "end_char": len(text)}
    unavailable = await client.post(url, json={**selection, "video_style": "illustrated"})
    assert unavailable.status_code == 400
    assert "回忆画卷" in unavailable.json()["detail"]
    upload = await client.post(url, json={**selection, "video_style": "uploaded"})
    assert upload.status_code == 400
    assert "背景图" in upload.json()["detail"]
    project = await test_db.get(Project, created["session"]["project_id"])
    project.metadata_json = {key: value for key, value in project.metadata_json.items() if key != "share_video_style"}
    await test_db.commit()
    legacy = await client.post(url, json=selection)
    assert legacy.status_code == 201
    assert legacy.json()["background_source"] == "classic"
    animated = await client.post(url, json={
        **selection, "video_style": "story_animation", "background_source": "custom",
        "background_style": "unused", "custom_background_asset_id": "not-needed",
    })
    assert animated.status_code == 201, animated.text
    assert animated.json()["background_status"] == "disabled"
    assert animated.json()["custom_background_asset_id"] is None


async def test_uploaded_style_keeps_asset_identity_and_separate_ai_jobs(client, test_db, tmp_path, monkeypatch):
    monkeypatch.setattr(share_video_jobs, "get_settings", lambda: _settings(ai_enabled=True))
    monkeypatch.setattr(share_video_custom_backgrounds, "DATA_DIR", tmp_path)
    monkeypatch.setattr(image_assets, "DATA_DIR", tmp_path)

    async def process(source_path, target_path):
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(source_path.read_bytes())

    monkeypatch.setattr(share_video_custom_backgrounds, "_process_background_image", process)
    text = "后来我们约起一起走。"
    created = await create_test_voice_transcript(client, test_db, text=text)
    project_id = created["session"]["project_id"]
    asset_ids = []
    for color in ("red", "blue"):
        source = BytesIO()
        Image.new("RGB", (8, 8), color).save(source, format="PNG")
        uploaded = await client.post(
            "/api/mobile/memoir/share-video/background-assets",
            data={"project_id": project_id}, files={"file": ("picture.png", source.getvalue(), "image/png")},
        )
        assert uploaded.status_code == 201, uploaded.text
        asset_ids.append(uploaded.json()["id"])
    url = f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}/share-video/jobs"
    selection = {"start_char": 0, "end_char": len(text)}
    jobs = []
    for asset_id in asset_ids:
        response = await client.post(url, json={
            **selection, "video_style": "uploaded", "custom_background_asset_id": asset_id,
        })
        assert response.status_code == 201, response.text
        assert response.json()["video_style"] == "uploaded"
        assert response.json()["background_source"] == "custom"
        jobs.append(response.json()["id"])
    repeated = await client.post(url, json={
        **selection, "video_style": "uploaded", "custom_background_asset_id": asset_ids[0],
    })
    assert repeated.json()["id"] == jobs[0]
    illustrated = await client.post(url, json={**selection, "video_style": "illustrated"})
    assert illustrated.status_code == 201
    assert len(set([*jobs, illustrated.json()["id"]])) == 3


def _plan(text, duration_ms):
    return ShareVideoStoryPlan.model_validate({
        "source_text_hash": sha256(text.encode()).hexdigest(),
        "duration_ms": duration_ms, "provider": "test", "model": "deepseek-flash",
        "actors": [{"id": "narrator", "figure": "group", "evidence": {"sentence_id": 0, "quote": "我们"}}],
        "shots": [{
            "sentence_ids": [0], "start_ms": 0, "end_ms": duration_ms,
            "setting": "street", "action": "walk", "gesture": "relaxed", "camera": "track",
            "framing": "medium", "actor_ids": ["narrator"], "active_actor_id": "narrator",
            "props": [], "evidence": [{"sentence_id": 0, "quote": text}], "movement_scale": 1.4,
        }],
    })


async def _animation_job(client, test_db):
    text = "先吃饭。后来我们约起一起走路。"
    created = await create_test_voice_transcript(client, test_db, text=text)
    start = text.index("后来")
    response = await client.post(
        f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}/share-video/jobs",
        json={"start_char": start, "end_char": len(text), "video_style": "story_animation"},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_animation_worker_skips_images_and_reuses_persisted_plan_on_retry(client, test_db, tmp_path, monkeypatch):
    monkeypatch.setattr(share_video_jobs, "get_settings", lambda: _settings())
    monkeypatch.setattr(share_video_jobs, "MEMOIR_SHARE_VIDEO_DIR", tmp_path)
    calls = []

    async def title(*_args, **_kwargs):
        return None

    async def plan(text, timings, duration_ms, *, ai_model, usage_context):
        calls.append((text, ai_model))
        assert timings[0]["start_ms"] == 0
        assert timings[-1]["end_ms"] == duration_ms
        assert usage_context.project_id
        return _plan(text, duration_ms)

    async def no_images(*_args, **_kwargs):
        pytest.fail("Animation must not prepare or generate image backgrounds")

    renders = []

    async def render(request):
        renders.append(request)
        assert request.video_style == "story_animation"
        assert request.animation_plan == _plan(request.selected_text, request.end_ms - request.start_ms)
        assert request.background_image_path is None and not request.background_segments
        persisted = await test_db.get(MemoirMaterialShareVideo, job["id"])
        assert persisted.metadata_json["animation_plan"]["source_text_hash"] == request.animation_plan.source_text_hash
        if len(renders) == 1:
            raise share_video_jobs.rendering.ShareVideoGenerationError("编码暂时失败")
        request.target_path.parent.mkdir(parents=True, exist_ok=True)
        request.target_path.write_bytes(b"rendered animation")

    monkeypatch.setattr(share_video_jobs, "_suggest_share_video_title", title)
    monkeypatch.setattr(share_video_jobs, "plan_share_video_story", plan)
    monkeypatch.setattr(share_video_jobs.background_orchestration, "prepare_share_video_background", no_images)
    monkeypatch.setattr(share_video_jobs.rendering, "render_share_video_clip", render)
    job = await _animation_job(client, test_db)
    assert job["start_ms"] > 0
    for expected in (False, True):
        persisted = await test_db.get(MemoirMaterialShareVideo, job["id"])
        lease = await share_video_jobs.claim_share_video_job(
            test_db, job["id"], now=persisted.run_after, lease_seconds=60, max_attempts=3,
        )
        assert lease is not None
        assert await share_video_jobs.run_share_video_job_with_session(
            test_db, lease, lease_seconds=60, max_attempts=3, retry_seconds=1,
        ) is expected
        test_db.expire_all()
    assert len(calls) == 1
    assert len(renders) == 2
    detail = (await client.get(f"/api/mobile/memoir/share-video/jobs/{job['id']}")).json()
    assert detail["status"] == "succeeded" and detail["video_style"] == "story_animation"


async def test_animation_planning_failure_is_visible_without_image_fallback(client, test_db, tmp_path, monkeypatch):
    monkeypatch.setattr(share_video_jobs, "get_settings", lambda: _settings())
    monkeypatch.setattr(share_video_jobs, "MEMOIR_SHARE_VIDEO_DIR", tmp_path)

    async def title(*_args, **_kwargs):
        return None

    async def invalid_plan(*_args, **_kwargs):
        raise ShareVideoStoryPlanningError("分镜引用不属于所选讲述")

    async def never_render(*_args, **_kwargs):
        pytest.fail("Invalid animation must fail instead of changing style")

    monkeypatch.setattr(share_video_jobs, "_suggest_share_video_title", title)
    monkeypatch.setattr(share_video_jobs, "plan_share_video_story", invalid_plan)
    monkeypatch.setattr(share_video_jobs.rendering, "render_share_video_clip", never_render)
    monkeypatch.setattr(share_video_jobs.background_orchestration, "prepare_share_video_background", never_render)
    job = await _animation_job(client, test_db)
    lease = await share_video_jobs.claim_share_video_job(test_db, job["id"], lease_seconds=60, max_attempts=1)
    assert lease is not None
    assert not await share_video_jobs.run_share_video_job_with_session(
        test_db, lease, lease_seconds=60, max_attempts=1, retry_seconds=1,
    )
    test_db.expire_all()
    detail = (await client.get(f"/api/mobile/memoir/share-video/jobs/{job['id']}")).json()
    assert detail["status"] == "failed" and detail["video_style"] == "story_animation"
    assert "分镜引用不属于所选讲述" in detail["error_message"]
    assert detail["output"] is None
