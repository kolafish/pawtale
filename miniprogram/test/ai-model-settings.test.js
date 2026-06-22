require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

const {
  DEFAULT_AI_MODEL,
  aiModelLabel,
  normalizeAIModel,
  normalizeAIModelSettings,
  readSelectedAIModel,
  saveSelectedAIModel,
} = require("../services/ai-model-settings.ts");

test("normalizes supported AI models", () => {
  assert.equal(normalizeAIModel(" deepseek-v4-flash "), "deepseek-v4-flash");
  assert.equal(normalizeAIModel("deepseek-v4-pro"), "deepseek-v4-pro");
  assert.equal(normalizeAIModel("doubao-seed-2-0-pro-260215"), "doubao-seed-2-0-pro-260215");
  assert.equal(normalizeAIModel("unknown-model"), DEFAULT_AI_MODEL);
  assert.equal(normalizeAIModel(null), DEFAULT_AI_MODEL);
});

test("normalizes persisted AI model settings", () => {
  assert.deepEqual(normalizeAIModelSettings({ model: "doubao-seed-2-0-pro-260215" }), {
    model: "doubao-seed-2-0-pro-260215",
  });
  assert.deepEqual(normalizeAIModelSettings({ model: "bad" }), { model: DEFAULT_AI_MODEL });
  assert.deepEqual(normalizeAIModelSettings("bad"), { model: DEFAULT_AI_MODEL });
});

test("returns labels with default fallback", () => {
  assert.equal(aiModelLabel("deepseek-v4-pro"), "DeepSeek V4 Pro");
  assert.equal(aiModelLabel("bad"), "DeepSeek V4 Pro");
});

test("reads and saves selected AI model", () => {
  let stored = {};
  global.wx = {
    getStorageSync() {
      return stored;
    },
    setStorageSync(_key, value) {
      stored = value;
    },
  };

  assert.equal(readSelectedAIModel(), DEFAULT_AI_MODEL);
  assert.equal(saveSelectedAIModel("deepseek-v4-flash"), "deepseek-v4-flash");
  assert.deepEqual(stored, { model: "deepseek-v4-flash" });
  assert.equal(readSelectedAIModel(), "deepseek-v4-flash");
  delete global.wx;
});
