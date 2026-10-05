import asyncio
from dataclasses import replace
from hashlib import sha256
from io import BytesIO
import math
from pathlib import Path
import struct
import subprocess
import sys
import wave

from PIL import Image, ImageChops
import pytest

from app.services.memoir import share_video_rendering as rendering
from app.services.memoir.share_video_story_plan import ShareVideoStoryPlan


def _ride_plan(text: str) -> ShareVideoStoryPlan:
    return ShareVideoStoryPlan.model_validate({
        "source_text_hash": sha256(text.encode()).hexdigest(),
        "duration_ms": 1000, "provider": "test", "model": "test",
        "actors": [{"id": "companions", "figure": "group",
                    "evidence": {"sentence_id": 0, "quote": "我们"}}],
        "shots": [{
            "sentence_ids": [0], "start_ms": 0, "end_ms": 1000,
            "setting": "vehicle", "action": "ride", "gesture": "relaxed", "camera": "track",
            "framing": "medium", "actor_ids": ["companions"], "active_actor_id": "companions",
            "props": [], "evidence": [{"sentence_id": 0, "quote": text}], "movement_scale": 1.2,
        }],
    })


async def test_story_video_encodes_moving_scenes_with_the_same_cut_original_audio(tmp_path: Path):
    ffmpeg = rendering._ffmpeg_executable()
    if not rendering._ffmpeg_filter_available(ffmpeg, "subtitles"):
        pytest.skip("This encoder has no subtitle filter")
    audio = tmp_path / "source.wav"
    rate = 48000
    # A distinct middle second proves that the shared, nonzero source range
    # is cut; the surrounding seconds must not enter either video.
    with wave.open(str(audio), "wb") as wav:
        wav.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        wav.writeframes(b"".join(struct.pack("<h", round(9000 * math.sin(
            math.tau * (440 if rate <= i < 2*rate else 880) * i / rate
        ))) for i in range(3*rate)))
    text = "我们坐车出发。"
    request = rendering.ShareVideoRenderRequest(
        source_audio_path=audio, source_audio_mime_type="audio/wav",
        target_path=tmp_path / "story.mp4", selected_text=text,
        char_timings=[{"start_ms": 1000+round(i*1000/len(text)),
                       "end_ms": 1000+round((i+1)*1000/len(text))} for i in range(len(text))],
        start_ms=1000, end_ms=2000, title="相约出发",
        video_style="story_animation", animation_plan=_ride_plan(text),
    )
    await rendering.render_share_video_clip(request)
    reference = replace(request, target_path=tmp_path / "reference.mp4",
                        video_style="illustrated", animation_plan=None)
    await rendering.render_share_video_clip(reference)
    def decoded_audio(path):
        return subprocess.check_output([ffmpeg, "-v", "error", "-i", str(path), "-map", "0:a:0",
                                        "-f", "s16le", "-acodec", "pcm_s16le", "-"])
    assert decoded_audio(request.target_path) == decoded_audio(reference.target_path)
    def frame(at):
        data = subprocess.check_output([ffmpeg, "-v", "error", "-ss", str(at), "-i", str(request.target_path),
                                        "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"])
        return Image.open(BytesIO(data)).convert("RGB")
    first, later = frame(1.1), frame(1.6)
    assert first.size == later.size == (1280, 720)
    assert ImageChops.difference(first.crop((0,100,1280,500)), later.crop((0,100,1280,500))).getbbox()


async def test_cancelled_frame_encoding_stops_the_child_process(monkeypatch):
    started = asyncio.Event()
    captured = []
    spawn = asyncio.create_subprocess_exec
    async def remember_process(*args, **kwargs):
        process = await spawn(*args, **kwargs)
        captured.append(process)
        started.set()
        return process
    monkeypatch.setattr(rendering.asyncio, "create_subprocess_exec", remember_process)
    task = asyncio.create_task(rendering._run_ffmpeg(
        [sys.executable, "-c", "import sys,time;sys.stdin.buffer.read(1);time.sleep(30)"],
        frames=(b"x"*1_000_000 for _ in range(1000)),
    ))
    await asyncio.wait_for(started.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)
    assert captured[0].returncode is not None
