require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

const {
  transcriptAudioText,
  transcriptDurationText,
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
