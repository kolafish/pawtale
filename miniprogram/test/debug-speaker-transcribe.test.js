require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");
const { clearModule, mockModule } = require("./page-harness");

function debugSpeakerModule() {
  delete require.cache[require.resolve("../services/debug-speaker-transcribe.ts")];
  return require("../services/debug-speaker-transcribe.ts");
}

function debugSpeakerModuleWithSocketMocks() {
  mockModule("../services/request.ts", {
    ApiError: class ApiError extends Error {},
    cleanBaseUrl: (value) => value.replace(/\/$/, ""),
    readBackendConfig: () => ({ wsBaseUrl: "wss://example.test" }),
  });
  mockModule("../services/memoir-voice-api.ts", {
    backendSessionCookieHeader: async () => "session=abc",
  });
  mockModule("../services/wechat-privacy.ts", {
    wechatRecorderErrorMessage: (message) => message || "录音失败",
  });
  clearModule("../services/debug-speaker-transcribe.ts");
  return require("../services/debug-speaker-transcribe.ts");
}

test("debug speaker gateway events normalize invalid payloads", () => {
  const { normalizeDebugSpeakerGatewayEvent } = debugSpeakerModule();

  assert.deepEqual(normalizeDebugSpeakerGatewayEvent("bad"), {
    type: "session.error",
    error: "invalid_debug_speaker_event",
  });
  assert.deepEqual(normalizeDebugSpeakerGatewayEvent({ text: "missing type" }), {
    type: "session.error",
    text: "missing type",
  });
});

test("debug speaker titles keep session-local speaker labels", () => {
  const { debugSpeakerTitle } = debugSpeakerModule();

  assert.equal(debugSpeakerTitle("speaker_1", 1), "说话人 1");
  assert.equal(debugSpeakerTitle("speaker_2", 2), "说话人 2");
  assert.equal(debugSpeakerTitle("speaker_unknown", 1), "未分辨说话人");
});

test("debug speaker client releases recorder when socket errors", async () => {
  const socketHandlers = {};
  let closeCalls = 0;
  global.wx = {
    connectSocket() {
      return {
        onOpen(callback) {
          socketHandlers.open = callback;
        },
        onMessage(callback) {
          socketHandlers.message = callback;
        },
        onError(callback) {
          socketHandlers.error = callback;
        },
        onClose(callback) {
          socketHandlers.close = callback;
        },
        send() {},
        close() {
          closeCalls += 1;
        },
      };
    },
  };
  const recorder = {
    handlers: null,
    stopCalls: 0,
    owned: false,
    start(_ownerId, _options, handlers) {
      this.handlers = handlers;
      this.owned = true;
      return true;
    },
    stop() {
      this.stopCalls += 1;
      this.owned = false;
    },
    isOwnedBy() {
      return this.owned;
    },
  };
  const statuses = [];
  const errors = [];
  const { DebugSpeakerTranscribeClient } = debugSpeakerModuleWithSocketMocks();
  const client = new DebugSpeakerTranscribeClient({
    onStatus: (status) => statuses.push(status),
    onStats: () => {},
    onSpeakerEvent: () => {},
    onError: (message) => errors.push(message),
  }, recorder);

  const started = client.start();
  await Promise.resolve();
  socketHandlers.open();
  recorder.handlers.onStart();
  await started;
  socketHandlers.error({ errMsg: "socket down" });

  assert.ok(recorder.stopCalls >= 1);
  assert.equal(closeCalls, 1);
  assert.deepEqual(errors, ["socket down"]);
  assert.ok(statuses.includes("listening"));
  assert.equal(statuses.at(-1), "error");
  delete global.wx;
});
