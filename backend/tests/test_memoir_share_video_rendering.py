import asyncio
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


async def test_opening_hook_plays_before_the_narration(tmp_path: Path):
    ffmpeg = rendering._ffmpeg_executable()
    audio = tmp_path / "source.wav"
    rate = 16000
    with wave.open(str(audio), "wb") as wav:
        wav.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        # Audio after the clip must not reach the closing card.
        wav.writeframes(b"".join(struct.pack("<h", round(9000 * math.sin(math.tau * 440 * i / rate)))
                                 for i in range(4 * rate)))
    text = "我们坐车出发。"
    request = rendering.ShareVideoRenderRequest(
        source_audio_path=audio, source_audio_mime_type="audio/wav",
        target_path=tmp_path / "hook.mp4", selected_text=text,
        char_timings=[{"start_ms": round(i*1000/len(text)), "end_ms": round((i+1)*1000/len(text))}
                      for i in range(len(text))],
        start_ms=0, end_ms=1000, hook_text=text,
    )
    await rendering.render_share_video_clip(request)
    hook_seconds = rendering._hook_seconds(text)
    pcm = subprocess.check_output([ffmpeg, "-v", "error", "-i", str(request.target_path), "-map", "0:a:0",
                                   "-ac", "1", "-ar", "8000", "-f", "s16le", "-acodec", "pcm_s16le", "-"])
    samples = struct.unpack(f"<{len(pcm)//2}h", pcm)
    def loudest(start, end):
        return max(abs(value) for value in samples[round(start*8000):round(end*8000)])
    assert loudest(0.1, hook_seconds - 0.2) < 200
    assert loudest(hook_seconds + 0.2, hook_seconds + 0.8) > 2000
    assert loudest(hook_seconds + 1.3, hook_seconds + 2.5) < 200


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


def test_closing_card_prints_the_mini_program_code_when_available():
    code = BytesIO()
    Image.new("RGBA", (430, 430), (20, 20, 20, 255)).save(code, format="PNG")
    code_area = (300, 600, 420, 720)

    with_code = rendering._end_card("相约出发", "余老师", code.getvalue()).crop(code_area).convert("L")
    without_code = rendering._end_card("相约出发", "余老师").crop(code_area).convert("L")
    unreadable = rendering._end_card("相约出发", "余老师", b"not an image").crop(code_area).convert("L")

    assert with_code.getextrema()[1] < 60
    assert without_code.getextrema()[0] > 150
    assert ImageChops.difference(unreadable, without_code).getbbox() is None
