require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

const { splitTextByPlaybackProgress } = require("../services/tts-text-progress.ts");

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
