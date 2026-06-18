require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

const {
  normalizeTTSSettings,
  normalizeTTSVoice,
  ttsVoiceLabel,
} = require("../services/tts-settings.ts");

test("normalizes supported TTS voices", () => {
  assert.equal(normalizeTTSVoice(" zh_female_vv_uranus_bigtts "), "zh_female_vv_uranus_bigtts");
  assert.equal(normalizeTTSVoice("zh_female_xiaohe_uranus_bigtts"), "zh_female_xiaohe_uranus_bigtts");
  assert.equal(normalizeTTSVoice("cixingnansheng"), "");
  assert.equal(normalizeTTSVoice("unknown-voice"), "");
  assert.equal(normalizeTTSVoice(null), "");
});

test("normalizes persisted TTS settings", () => {
  assert.deepEqual(normalizeTTSSettings({ voice: "zh_male_m191_uranus_bigtts" }), {
    voice: "zh_male_m191_uranus_bigtts",
  });
  assert.deepEqual(normalizeTTSSettings({ voice: "bad" }), { voice: "" });
  assert.deepEqual(normalizeTTSSettings("bad"), { voice: "" });
});

test("returns labels for TTS voices with default fallback", () => {
  assert.equal(ttsVoiceLabel("zh_female_vv_uranus_bigtts"), "通用女声");
  assert.equal(ttsVoiceLabel("bad"), "默认音色");
});
