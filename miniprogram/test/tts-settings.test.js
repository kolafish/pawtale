require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

const {
  bookTTSRequestFromSettings,
  normalizeTTSSettings,
  normalizeScopedTTSSettings,
  normalizeTTSSpeedRatio,
  normalizeTTSVoice,
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
      book: { voice: "cixingnansheng", speedRatio: 1.5 },
    },
  );
  assert.deepEqual(
    normalizeScopedTTSSettings({ voice: "zh_female_vv_uranus_bigtts", speedRatio: 1.5 }),
    {
      chat: { voice: "zh_female_vv_uranus_bigtts", speedRatio: 1.5 },
      book: { voice: "wenrounvsheng", speedRatio: 1.5 },
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

test("builds book TTS requests without redundant defaults", () => {
  assert.deepEqual(
    bookTTSRequestFromSettings({
      voice: "zh_female_xiaohe_uranus_bigtts",
      speedRatio: 1.5,
    }),
    {
      voice: "linjiajiejie",
      speed_ratio: 1.5,
    },
  );
  assert.deepEqual(
    bookTTSRequestFromSettings({ voice: "", speedRatio: 1 }),
    {},
  );
});

test("keeps narration voices separate from conversation and saves the migration", () => {
  for (const voice of ["wenrounvsheng", "linjiajiejie", "cixingnansheng"]) {
    assert.equal(normalizeTTSVoice(voice, "book"), voice);
    assert.equal(normalizeTTSVoice(voice, "chat"), "");
  }
  let stored = {
    chat: { voice: "zh_female_xiaohe_uranus_bigtts", speedRatio: 0.7 },
    book: { voice: "zh_male_m191_uranus_bigtts", speedRatio: 1.5 },
  };
  global.wx = {
    getStorageSync: () => stored,
    setStorageSync: (_key, value) => { stored = value; },
  };
  const { readBookTTSRequest, saveScopedTTSSettings } = require("../services/tts-settings.ts");
  assert.deepEqual(readBookTTSRequest(), { voice: "cixingnansheng", speed_ratio: 1.5 });
  saveScopedTTSSettings("book", { voice: "wenrounvsheng", speedRatio: 1 });
  assert.deepEqual(stored.chat, { voice: "zh_female_xiaohe_uranus_bigtts", speedRatio: 0.7 });
  assert.equal(stored.book.voice, "wenrounvsheng");
  assert.deepEqual(readBookTTSRequest(), { voice: "wenrounvsheng" });
});
