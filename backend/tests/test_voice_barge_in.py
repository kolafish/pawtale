import base64
import wave
from pathlib import Path

import numpy as np
import pytest

from app.services.asr import barge_in as barge_in_module
from app.services.asr.barge_in import BargeInDetector, load_barge_in_model
from app.services.asr.gateway import VoiceGatewayClientError, VoiceGatewaySession
from tests.test_memoir_voice_gateway import FakeGatewayClient, FakeStreamingASRClient

FIXTURES = Path(__file__).parent / "fixtures" / "barge_in"


def speech_pcm():
    with wave.open(str(FIXTURES / "wait.wav")) as audio:
        return b"\0" * 25600 + audio.readframes(audio.getnframes())


def test_bundled_model_detects_speech_across_pcm_frame_boundaries():
    pcm = speech_pcm()
    for size in (1024, 4096, 3200):
        detector = BargeInDetector()
        hits = [detector.feed(pcm[i:i + size]) for i in range(0, len(pcm), size)]
        assert sum(hits) == 1
        assert detector.feed(pcm) is False


def test_speech_requires_five_consecutive_model_windows(monkeypatch):
    class AlwaysSpeechModel:
        def run(self, _outputs, inputs):
            return np.array([[0.95]], dtype=np.float32), inputs["state"]

    monkeypatch.setattr(barge_in_module, "load_barge_in_model", lambda: AlwaysSpeechModel())
    detector = BargeInDetector()
    detector.feed(b"\0" * barge_in_module.WARMUP_WINDOWS * barge_in_module.WINDOW_SAMPLES * 2)
    speech_window = np.full(barge_in_module.WINDOW_SAMPLES, 0.2, dtype=np.float32)
    speech_pcm = (speech_window * 32767).astype("<i2").tobytes()

    for _ in range(4):
        assert detector.feed(speech_pcm) is False
    assert detector.feed(speech_pcm) is True


def test_streams_share_weights_but_never_recurrent_state():
    first, second = BargeInDetector(), BargeInDetector()
    assert first.model is second.model is load_barge_in_model()
    first.feed(speech_pcm())
    assert first.triggered
    assert not second.state.any()
    assert not second.feed(b"\0" * 64000)


def test_loud_non_speech_does_not_trigger_the_real_model():
    noise = np.random.default_rng(42).normal(0, 0.12, 64000).clip(-1, 1)
    pcm = b"\0" * 25600 + (noise * 32767).astype("<i2").tobytes()
    detector = BargeInDetector()
    assert not any(detector.feed(pcm[i:i + 4096]) for i in range(0, len(pcm), 4096))


def test_calibrated_playback_fixture_does_not_interrupt_itself():
    with wave.open(str(FIXTURES / "assistant.wav")) as audio:
        pcm = audio.readframes(audio.getnframes())
    detector = BargeInDetector()
    assert not any(detector.feed(pcm[i:i + 4096]) for i in range(0, len(pcm), 4096))


async def test_monitor_audio_never_starts_asr_or_enters_audio_evidence():
    client, asr = FakeGatewayClient(), FakeStreamingASRClient()
    evidence = []

    async def save_audio(pcm):
        evidence.append(pcm)

    gateway = VoiceGatewaySession(client=client, asr_client_factory=lambda: asr,
                                  on_audio_frame=save_audio, barge_in_detector_factory=BargeInDetector)
    await gateway._handle_client_message({"type": "barge_in.start", "monitor_id": "a"})
    pcm = speech_pcm()
    for offset in range(0, len(pcm), 4096):
        await gateway._handle_client_message({"type": "barge_in.frame", "monitor_id": "a",
                                              "audio": base64.b64encode(pcm[offset:offset + 4096]).decode()})
    hits = [event for event in client.sent if event["type"] == "barge_in.speech"]
    assert len(hits) == 1
    assert hits[0]["monitor_id"] == "a"
    assert 640 <= hits[0]["start_ms"] <= 1200
    assert gateway._asr_client is None
    assert evidence == []
    await gateway._handle_client_message({"type": "barge_in.stop", "monitor_id": "a"})
    assert gateway._barge_in_detector is None


async def test_old_monitor_stop_and_audio_cannot_affect_new_monitor():
    gateway = VoiceGatewaySession(client=FakeGatewayClient(), asr_client_factory=FakeStreamingASRClient,
                                  barge_in_detector_factory=BargeInDetector)
    await gateway._handle_client_message({"type": "barge_in.start", "monitor_id": "b"})
    detector = gateway._barge_in_detector
    await gateway._handle_client_message({"type": "barge_in.stop", "monitor_id": "a"})
    await gateway._handle_client_message({"type": "barge_in.frame", "monitor_id": "a", "audio": "bad"})
    assert gateway._barge_in_detector is detector
    assert detector.windows == 0
    await gateway._handle_client_message({"type": "session.start"})
    assert gateway._barge_in_detector is None
    await gateway._shutdown()


@pytest.mark.parametrize("audio", ["!", "YQ==", "", "AAAA" * 12000])
async def test_monitor_requires_bounded_pcm16_frames(audio):
    gateway = VoiceGatewaySession(client=FakeGatewayClient(), asr_client_factory=FakeStreamingASRClient,
                                  barge_in_detector_factory=BargeInDetector)
    await gateway._handle_client_message({"type": "barge_in.start", "monitor_id": "a"})
    with pytest.raises(VoiceGatewayClientError, match="invalid_barge_in_audio"):
        await gateway._handle_client_message({"type": "barge_in.frame", "monitor_id": "a", "audio": audio})
