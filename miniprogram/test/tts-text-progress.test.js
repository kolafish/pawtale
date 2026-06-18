require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

const {
  estimateTTSPlaybackDurationSeconds,
  splitTextByAudioPlaybackProgress,
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

test("splits TTS text by fallback duration when audio duration is unavailable", () => {
  assert.deepEqual(splitTextByAudioPlaybackProgress("你好世界", 1, 0, {
    fallbackDurationSeconds: 4,
  }), {
    spokenText: "你",
    currentText: "好",
    remainingText: "世界",
    progressPercent: 25,
  });
});

test("estimates assistant TTS duration with a conversational question pace", () => {
  assert.equal(estimateTTSPlaybackDurationSeconds("你好吗？"), 0.8);
  assert.equal(estimateTTSPlaybackDurationSeconds("你还记得那天发生什么吗？"), 2.06);
});

test("estimates missing TTS duration so playback progress can still move", () => {
  const progress = splitTextByAudioPlaybackProgress("你好吗朋友", 0.8, 0, {
    estimateMissingDuration: true,
  });

  assert.notEqual(progress.currentText, "你");
  assert.ok(progress.progressPercent > 0);
});

test("uses estimated duration instead of a coarse slower fallback duration", () => {
  const progress = splitTextByAudioPlaybackProgress("你还记得那天发生什么吗？", 1, 0, {
    fallbackDurationSeconds: 5,
    estimateMissingDuration: true,
  });

  assert.equal(progress.progressPercent, 49);
  assert.equal(progress.currentText, "天");
});

test("keeps close fallback duration instead of forcing the estimate", () => {
  const progress = splitTextByAudioPlaybackProgress("你还记得那天发生什么吗？", 1, 0, {
    fallbackDurationSeconds: 2.2,
    estimateMissingDuration: true,
  });

  assert.equal(progress.progressPercent, 45);
  assert.equal(progress.currentText, "天");
});

test("uses alignment in audio playback progress when available", () => {
  const alignment = [
    { text: "你", start_ms: 0, end_ms: 120, text_start: 0, text_end: 1 },
    { text: "好", start_ms: 120, end_ms: 260, text_start: 1, text_end: 2 },
  ];

  assert.deepEqual(splitTextByAudioPlaybackProgress("你好", 0.15, 0, {
    alignment,
    estimateMissingDuration: true,
  }), {
    spokenText: "你",
    currentText: "好",
    remainingText: "",
    progressPercent: 58,
  });
});
