"""Streaming speech detection during assistant playback (16 kHz mono PCM16).

The model session is shared; recurrent state and playback calibration belong to
one monitor. Calibration limits playback echo, but is not acoustic echo cancellation.
"""
from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

import numpy as np
import onnxruntime as ort

MODEL_PATH = Path(__file__).with_name("models") / "silero_vad_16k_op15.onnx"
MODEL_SHA256 = "7ed98ddbad84ccac4cd0aeb3099049280713df825c610a8ed34543318f1b2c49"
WINDOW_SAMPLES = 512
WINDOW_MS = 32
WARMUP_WINDOWS = 20
SPEECH_THRESHOLD = 0.8
TRIGGER_WINDOWS = 4


@lru_cache(maxsize=1)
def load_barge_in_model() -> ort.InferenceSession:
    model = MODEL_PATH.read_bytes()
    if hashlib.sha256(model).hexdigest() != MODEL_SHA256:
        raise RuntimeError("silero_vad_model_checksum_mismatch")
    options = ort.SessionOptions()
    options.intra_op_num_threads = 1
    options.inter_op_num_threads = 1
    return ort.InferenceSession(model, sess_options=options, providers=["CPUExecutionProvider"])


class BargeInDetector:
    def __init__(self) -> None:
        self.model = load_barge_in_model()
        self.state = np.zeros((2, 1, 128), dtype=np.float32)
        self.context = np.zeros((1, 64), dtype=np.float32)
        self.pending = b""
        self.windows = 0
        self.baseline_sum = 0.0
        self.baseline_peak = 0.0
        self.speech_windows = 0
        self.triggered = False

    def feed(self, pcm: bytes) -> bool:
        if self.triggered:
            return False
        self.pending += pcm
        while len(self.pending) >= WINDOW_SAMPLES * 2:
            window, self.pending = self.pending[:1024], self.pending[1024:]
            samples = np.frombuffer(window, dtype="<i2").astype(np.float32) / 32768.0
            level = float(np.sqrt(np.mean(samples * samples)))
            inputs = np.concatenate((self.context, samples.reshape(1, -1)), axis=1)
            output, self.state = self.model.run(None, {
                "input": inputs,
                "state": self.state,
                "sr": np.array(16000, dtype=np.int64),
            })
            self.context = inputs[:, -64:].copy()
            self.windows += 1
            if self.windows <= WARMUP_WINDOWS:
                self.baseline_sum += level
                self.baseline_peak = max(self.baseline_peak, level)
                continue
            echo_level = max(self.baseline_sum / WARMUP_WINDOWS * 1.65, self.baseline_peak * 1.18)
            speech = float(output[0, 0]) >= SPEECH_THRESHOLD and level > echo_level
            self.speech_windows = self.speech_windows + 1 if speech else 0
            if self.speech_windows >= TRIGGER_WINDOWS:
                self.triggered = True
                self.pending = b""
                return True
        return False
