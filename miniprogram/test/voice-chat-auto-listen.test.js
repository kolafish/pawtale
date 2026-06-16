require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

const { canAutoStartListeningAfterTTS } = require("../services/voice-chat-auto-listen.ts");

function state(overrides = {}) {
  return {
    autoListenEnabled: true,
    status: "ready",
    recording: false,
    connecting: false,
    followUpLoading: false,
    ...overrides,
  };
}

test("allows auto listening after TTS only when the voice session can record", () => {
  assert.equal(canAutoStartListeningAfterTTS(state()), true);
  assert.equal(canAutoStartListeningAfterTTS(state({ status: "idle" })), false);
  assert.equal(canAutoStartListeningAfterTTS(state({ autoListenEnabled: false })), false);
  assert.equal(canAutoStartListeningAfterTTS(state({ recording: true })), false);
  assert.equal(canAutoStartListeningAfterTTS(state({ connecting: true })), false);
  assert.equal(canAutoStartListeningAfterTTS(state({ followUpLoading: true })), false);
  assert.equal(canAutoStartListeningAfterTTS(state({ status: "closed" })), false);
  assert.equal(canAutoStartListeningAfterTTS(state({ status: "error" })), false);
});
