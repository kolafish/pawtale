require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

function loadTranscriptsPage() {
  const transcriptsPath = require.resolve("../pages/transcripts/index.ts");
  delete require.cache[transcriptsPath];
  const previousPage = global.Page;
  global.Page = () => {};
  const mod = require(transcriptsPath);
  if (previousPage === undefined) {
    delete global.Page;
  } else {
    global.Page = previousPage;
  }
  return mod;
}

test("transcript audio summary only exposes duration", () => {
  const { transcriptAudioText } = loadTranscriptsPage();
  const summary = transcriptAudioText({
    audio_duration_seconds: 74,
    audio_file_id: "audio-1",
    audio_size_bytes: 491000,
    audio_mime_type: "audio/pcm",
  });

  assert.equal(summary, "时长 1 分 14 秒");
  assert.equal(summary.includes("音频已保存"), false);
  assert.equal(summary.includes("491"), false);
  assert.equal(summary.includes("PCM"), false);
});

test("transcript audio summary falls back to utterance timestamps", () => {
  const { transcriptAudioText } = loadTranscriptsPage();

  assert.equal(
    transcriptAudioText({
      audio_duration_seconds: null,
      start_ms: 1000,
      end_ms: 10500,
    }),
    "时长 10 秒",
  );
});
