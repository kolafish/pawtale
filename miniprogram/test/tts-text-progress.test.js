require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

const {
  splitTextByPlaybackProgress,
  splitTextByTimedAlignment,
} = require("../services/tts-text-progress.ts");

test("splits TTS text by playback progress", () => {
  assert.deepEqual(splitTextByPlaybackProgress("你好，世界", 0), {
    spokenText: "",
    currentText: "你",
    remainingText: "好，世界",
    progressPercent: 0,
  });
  assert.deepEqual(splitTextByPlaybackProgress("你好，世界", 0.5), {
    spokenText: "你好",
    currentText: "，",
    remainingText: "世界",
    progressPercent: 50,
  });
  assert.deepEqual(splitTextByPlaybackProgress("你好，世界", 1), {
    spokenText: "你好，世界",
    currentText: "",
    remainingText: "",
    progressPercent: 100,
  });
});

test("clamps invalid TTS progress ratios", () => {
  assert.deepEqual(splitTextByPlaybackProgress("测试", -1), {
    spokenText: "",
    currentText: "测",
    remainingText: "试",
    progressPercent: 0,
  });
  assert.deepEqual(splitTextByPlaybackProgress("测试", Number.NaN), {
    spokenText: "",
    currentText: "测",
    remainingText: "试",
    progressPercent: 0,
  });
  assert.deepEqual(splitTextByPlaybackProgress("测试", 2), {
    spokenText: "测试",
    currentText: "",
    remainingText: "",
    progressPercent: 100,
  });
});

test("splits TTS text by forced alignment timestamps", () => {
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
