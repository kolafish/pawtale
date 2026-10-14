require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

const {
  transcriptAudioText,
  transcriptDurationText,
  transcriptTitleText,
} = require("../services/transcript-view-model.ts");

test("transcript view model formats duration and audio text consistently", () => {
  const item = {
    audio_duration_seconds: null,
    start_ms: 1000,
    end_ms: 10500,
  };

  assert.equal(transcriptDurationText(item), "10 秒");
  assert.equal(transcriptAudioText(item), "10 秒");
  assert.equal(transcriptAudioText(item, { includeDurationLabel: true }), "时长 10 秒");
  assert.equal(transcriptAudioText({ audio_duration_seconds: null }), "时长未知");
});

test("fallback transcript titles mark a truncated source without altering supplied titles", () => {
  const text = "那年我们回到老家修补房子，父亲和母亲一起挑水和泥巴，重新砌好了倒下的墙。";
  assert.equal(transcriptTitleText({ display_text: text }), `${text.slice(0, 32)}…`);
  assert.equal(transcriptTitleText({ display_text: "我们回老家了。" }), "我们回老家了。");
  assert.equal(transcriptTitleText({ summary_title: "回家修房子", display_text: text }), "回家修房子");
});
