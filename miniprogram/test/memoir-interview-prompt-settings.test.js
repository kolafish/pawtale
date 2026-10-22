require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

const {
  normalizeMemoirInterviewPromptVariant,
  readSelectedMemoirInterviewPromptVariant,
} = require("../services/memoir-interview-prompt-settings.ts");

test("maps the retired standalone prompt value to v4", () => {
  assert.equal(normalizeMemoirInterviewPromptVariant("standalone"), "v4");
  assert.equal(normalizeMemoirInterviewPromptVariant("v4"), "v4");
});

test("migrates a persisted standalone selection to v4", () => {
  let stored = { variant: "standalone" };
  global.wx = {
    getStorageSync() {
      return stored;
    },
    setStorageSync(_key, value) {
      stored = value;
    },
  };

  assert.equal(readSelectedMemoirInterviewPromptVariant(), "v4");
  assert.deepEqual(stored, { variant: "v4" });
  delete global.wx;
});
