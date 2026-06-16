require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

const {
  normalizeTTSSettings,
  normalizeTTSVoice,
  ttsVoiceLabel,
} = require("../services/tts-settings.ts");

test("normalizes supported TTS voices", () => {
  assert.equal(normalizeTTSVoice(" cixingnansheng "), "cixingnansheng");
  assert.equal(normalizeTTSVoice("wenrounansheng"), "wenrounansheng");
  assert.equal(normalizeTTSVoice("unknown-voice"), "");
  assert.equal(normalizeTTSVoice(null), "");
});

test("normalizes persisted TTS settings", () => {
  assert.deepEqual(normalizeTTSSettings({ voice: "qinqienvsheng" }), {
    voice: "qinqienvsheng",
  });
  assert.deepEqual(normalizeTTSSettings({ voice: "bad" }), { voice: "" });
  assert.deepEqual(normalizeTTSSettings("bad"), { voice: "" });
});

test("returns labels for TTS voices with default fallback", () => {
  assert.equal(ttsVoiceLabel("cixingnansheng"), "磁性男声");
  assert.equal(ttsVoiceLabel("bad"), "默认音色");
});
