require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

const {
  AI_MODEL_OPTIONS,
  DEFAULT_AI_MODEL,
  aiModelLabel,
  normalizeAIModel,
  normalizeAIModelSettings,
  readSelectedAIModel,
  saveSelectedAIModel,
} = require("../services/ai-model-settings.ts");

test("normalizes supported AI models", () => {
  assert.equal(normalizeAIModel(" deepseek-flash "), "deepseek-flash");
  assert.equal(normalizeAIModel("deepseek-v4-flash"), "deepseek-flash");
  assert.equal(normalizeAIModel("deepseek-v4-pro"), "deepseek-v4-pro");
  assert.equal(normalizeAIModel("claude-fable-5"), "claude-fable-5");
  assert.equal(normalizeAIModel("claude-opus-4-8"), "claude-opus-4-8");
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
  assert.equal(aiModelLabel("deepseek-flash"), "DeepSeek V4.1 Flash");
  assert.equal(aiModelLabel("deepseek-v4-flash"), "DeepSeek V4.1 Flash");
  assert.equal(aiModelLabel("deepseek-v4-pro"), "DeepSeek V4 Pro");
  assert.equal(aiModelLabel("claude-fable-5"), "Claude Fable 5");
  assert.equal(aiModelLabel("claude-opus-4-8"), "Claude Opus 4.8");
  assert.equal(aiModelLabel("bad"), "Claude Opus 4.8");
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
  assert.equal(saveSelectedAIModel("deepseek-v4-flash"), "deepseek-flash");
  assert.deepEqual(stored, { model: "deepseek-flash" });
  assert.equal(readSelectedAIModel(), "deepseek-flash");
  delete global.wx;
});

test("exposes the current DeepSeek models in the selector", () => {
  const deepseekOptions = AI_MODEL_OPTIONS.filter((option) => option.value.startsWith("deepseek"));
  assert.deepEqual(
    deepseekOptions.map(({ value, label }) => ({ value, label })),
    [
      { value: "deepseek-v4-pro", label: "DeepSeek V4 Pro" },
      { value: "deepseek-flash", label: "DeepSeek V4.1 Flash" },
    ],
  );
});
