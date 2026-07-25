require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

const {
  bookTTSRequestFromSettings,
  normalizeTTSSettings,
  normalizeScopedTTSSettings,
  normalizeTTSSpeedRatio,
  normalizeTTSVoice,
  ttsSpeedLabel,
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
  assert.deepEqual(normalizeTTSSettings({ voice: "zh_male_m191_uranus_bigtts", speedRatio: 0.7 }), {
    voice: "zh_male_m191_uranus_bigtts",
    speedRatio: 0.7,
  });
  assert.deepEqual(normalizeTTSSettings({ voice: "bad", speedRatio: 2 }), { voice: "", speedRatio: 1 });
  assert.deepEqual(normalizeTTSSettings("bad"), { voice: "", speedRatio: 1 });
});

test("normalizes scoped TTS settings and migrates the legacy flat shape", () => {
  assert.deepEqual(
    normalizeScopedTTSSettings({
      chat: { voice: "zh_female_xiaohe_uranus_bigtts", speedRatio: 0.7 },
      book: { voice: "zh_male_m191_uranus_bigtts", speedRatio: 1.5 },
    }),
    {
      chat: { voice: "zh_female_xiaohe_uranus_bigtts", speedRatio: 0.7 },
      book: { voice: "zh_male_m191_uranus_bigtts", speedRatio: 1.5 },
    },
  );
  assert.deepEqual(
    normalizeScopedTTSSettings({ voice: "zh_female_vv_uranus_bigtts", speedRatio: 1.5 }),
    {
      chat: { voice: "zh_female_vv_uranus_bigtts", speedRatio: 1.5 },
      book: { voice: "zh_female_vv_uranus_bigtts", speedRatio: 1.5 },
    },
  );
  assert.deepEqual(normalizeScopedTTSSettings("bad"), {
    chat: { voice: "", speedRatio: 1 },
    book: { voice: "", speedRatio: 1 },
  });
});

test("normalizes supported TTS speed ratios", () => {
  assert.equal(normalizeTTSSpeedRatio("0.7"), 0.7);
  assert.equal(normalizeTTSSpeedRatio(1), 1);
  assert.equal(normalizeTTSSpeedRatio(1.5), 1.5);
  assert.equal(normalizeTTSSpeedRatio(1.25), 1);
});

test("returns labels for TTS voices with default fallback", () => {
  assert.equal(ttsVoiceLabel("zh_female_vv_uranus_bigtts"), "通用女声");
  assert.equal(ttsVoiceLabel("bad"), "默认音色");
  assert.equal(ttsSpeedLabel(0.7), "慢速 0.7x");
  assert.equal(ttsSpeedLabel(1.5), "快速 1.5x");
  assert.equal(ttsSpeedLabel(1.25), "正常 1.0x");
});

test("builds book TTS requests without redundant defaults", () => {
  assert.deepEqual(
    bookTTSRequestFromSettings({
      voice: "zh_female_xiaohe_uranus_bigtts",
      speedRatio: 1.5,
    }),
    {
      voice: "zh_female_xiaohe_uranus_bigtts",
      speed_ratio: 1.5,
    },
  );
  assert.deepEqual(
    bookTTSRequestFromSettings({ voice: "", speedRatio: 1 }),
    {},
  );
});
