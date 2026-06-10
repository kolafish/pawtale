require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

function playerModule() {
  delete require.cache[require.resolve("../services/memoir-tts-player.ts")];
  return require("../services/memoir-tts-player.ts");
}

test("builds stable TTS playback source keys", () => {
  const { memoirTtsSourceKey } = playerModule();

  assert.equal(
    memoirTtsSourceKey({ source_type: "question", source_id: "question_1" }),
    "question:question_1",
  );
});

test("normalizes TTS playback speed to supported steps", () => {
  const { normalizeMemoirTtsPlaybackSpeed } = playerModule();

  assert.equal(normalizeMemoirTtsPlaybackSpeed(0.6), 0.75);
  assert.equal(normalizeMemoirTtsPlaybackSpeed(1.18), 1.25);
  assert.equal(normalizeMemoirTtsPlaybackSpeed(9), 2);
  assert.equal(normalizeMemoirTtsPlaybackSpeed(Number.NaN), 1);
});

test("cycles TTS playback speed through all supported steps", () => {
  const { nextMemoirTtsPlaybackSpeed } = playerModule();

  assert.equal(nextMemoirTtsPlaybackSpeed(0.75), 1);
  assert.equal(nextMemoirTtsPlaybackSpeed(1), 1.25);
  assert.equal(nextMemoirTtsPlaybackSpeed(1.25), 1.5);
  assert.equal(nextMemoirTtsPlaybackSpeed(1.5), 2);
  assert.equal(nextMemoirTtsPlaybackSpeed(2), 0.75);
});
