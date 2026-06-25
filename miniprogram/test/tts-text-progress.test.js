require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

const {
  splitTextByAudioPlaybackProgress,
  splitTextByTimedAlignment,
} = require("../services/tts-text-progress.ts");

test("splits TTS text by native timing timestamps", () => {
  const alignment = [
    { text: "你", start_ms: 0, end_ms: 120, text_start: 0, text_end: 1 },
    { text: "好", start_ms: 120, end_ms: 260, text_start: 1, text_end: 2 },
    { text: "呀", start_ms: 320, end_ms: 460, text_start: 2, text_end: 3 },
  ];

  assert.deepEqual(splitTextByTimedAlignment("你好呀", 150, alignment), {
    spokenText: "你",
    currentText: "好",
    remainingText: "呀",
    progressPercent: 33,
  });
  assert.deepEqual(splitTextByTimedAlignment("你好呀", 300, alignment), {
    spokenText: "你",
    currentText: "好",
    remainingText: "呀",
    progressPercent: 65,
  });
  assert.deepEqual(splitTextByTimedAlignment("你好呀", 500, alignment), {
    spokenText: "你好呀",
    currentText: "",
    remainingText: "",
    progressPercent: 100,
  });
});

test("uses alignment in audio playback progress when available", () => {
  const alignment = [
    { text: "你", start_ms: 0, end_ms: 120, text_start: 0, text_end: 1 },
    { text: "好", start_ms: 120, end_ms: 260, text_start: 1, text_end: 2 },
  ];

  assert.deepEqual(splitTextByAudioPlaybackProgress("你好", 0.15, 0, {
    alignment,
  }), {
    spokenText: "你",
    currentText: "好",
    remainingText: "",
    progressPercent: 58,
  });
});

test("estimates highlighted text without native timing", () => {
  assert.deepEqual(splitTextByAudioPlaybackProgress("你好，世界", 0.5, 2), {
    spokenText: "你",
    currentText: "好",
    remainingText: "，世界",
    progressPercent: 25,
  });
});

test("does not highlight text before the first aligned character starts", () => {
  const alignment = [
    { text: "你", start_ms: 300, end_ms: 420, text_start: 0, text_end: 1 },
    { text: "好", start_ms: 420, end_ms: 600, text_start: 1, text_end: 2 },
  ];

  assert.deepEqual(splitTextByTimedAlignment("你好", 100, alignment), {
    spokenText: "",
    currentText: "",
    remainingText: "你好",
    progressPercent: 17,
  });
});

test("falls back to estimated progress when alignment text does not match", () => {
  const alignment = [
    { text: "世界", start_ms: 0, end_ms: 400, text_start: 0, text_end: 2 },
  ];

  assert.deepEqual(splitTextByAudioPlaybackProgress("你好，世界", 0.5, 2, {
    alignment,
  }), {
    spokenText: "你",
    currentText: "好",
    remainingText: "，世界",
    progressPercent: 25,
  });
});
