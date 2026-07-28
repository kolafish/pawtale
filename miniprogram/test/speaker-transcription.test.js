require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");
const { clearModule, mockModule } = require("./page-harness");

function speakerTranscriptionModule() {
  delete require.cache[require.resolve("../services/speaker-transcription.ts")];
  return require("../services/speaker-transcription.ts");
}

function speakerTranscriptionModuleWithSocketMocks() {
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
  clearModule("../services/speaker-transcription.ts");
  return require("../services/speaker-transcription.ts");
}

test("speaker transcription gateway events normalize invalid payloads", () => {
  const { normalizeSpeakerTranscriptionGatewayEvent } = speakerTranscriptionModule();

  assert.deepEqual(normalizeSpeakerTranscriptionGatewayEvent("bad"), {
    type: "session.error",
    error: "invalid_speaker_transcription_event",
  });
  assert.deepEqual(normalizeSpeakerTranscriptionGatewayEvent({ text: "missing type" }), {
    type: "session.error",
    text: "missing type",
  });
});

test("speaker transcription titles keep session-local speaker labels", () => {
  const { speakerTitle } = speakerTranscriptionModule();

  assert.equal(speakerTitle("speaker_1", 1), "说话人 1");
  assert.equal(speakerTitle("speaker_2", 2), "说话人 2");
  assert.equal(speakerTitle("speaker_unknown", 1), "未分辨说话人");
});

test("speaker transcription gateway uses the product API route", () => {
  const { speakerTranscriptionGatewayUrl } = speakerTranscriptionModuleWithSocketMocks();

  assert.equal(
    speakerTranscriptionGatewayUrl(),
    "wss://example.test/api/mobile/memoir/speaker-transcription/gateway",
  );
});

test("speaker transcription client annotates speaker events with occurrence wall time", async () => {
  const socketHandlers = {};
  const originalNow = Date.now;
  Date.now = () => Date.UTC(2026, 6, 7, 5, 30, 0);
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
        close() {},
      };
    },
  };
  const recorder = {
    owned: false,
    start() {
      this.owned = true;
      return true;
    },
    stop() {
      this.owned = false;
    },
    isOwnedBy() {
      return this.owned;
    },
  };
  const speakerEvents = [];
  const { SpeakerTranscriptionClient } = speakerTranscriptionModuleWithSocketMocks();
  const client = new SpeakerTranscriptionClient({
    onStatus: () => {},
    onAudioLevel: () => {},
    onSpeakerEvent: (event) => speakerEvents.push(event),
    onError: () => {},
  }, recorder);

  try {
    const started = client.start();
    await Promise.resolve();
    socketHandlers.open();
    await started;
    socketHandlers.message({
      data: JSON.stringify({
        type: "speaker.final",
        speaker_id: "speaker_2",
        text: "现在应该是第八道了哈。",
        start_ms: 44_000,
        end_ms: 47_000,
      }),
    });

    assert.equal(speakerEvents.length, 1);
    assert.equal(speakerEvents[0].occurred_start_at_ms, Date.UTC(2026, 6, 7, 5, 30, 44));
    assert.equal(speakerEvents[0].occurred_end_at_ms, Date.UTC(2026, 6, 7, 5, 30, 47));
  } finally {
    client.close();
    delete global.wx;
    Date.now = originalNow;
  }
});

test("speaker transcription client keeps terminal socket statuses stable", async () => {
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
  const { SpeakerTranscriptionClient } = speakerTranscriptionModuleWithSocketMocks();
  const client = new SpeakerTranscriptionClient({
    onStatus: (status) => statuses.push(status),
    onAudioLevel: () => {},
    onSpeakerEvent: () => {},
    onError: (message) => errors.push(message),
  }, recorder);

  const started = client.start();
  await Promise.resolve();
  socketHandlers.open();
  recorder.handlers.onStart();
  await started;
  socketHandlers.error({ errMsg: "socket down" });
  socketHandlers.close();

  assert.ok(recorder.stopCalls >= 1);
  assert.equal(closeCalls, 1);
  assert.deepEqual(errors, ["socket down"]);
  assert.ok(statuses.includes("listening"));
  assert.equal(statuses.at(-1), "error");

  const stoppedStatuses = [];
  const stoppedClient = new SpeakerTranscriptionClient({
    onStatus: (status) => stoppedStatuses.push(status),
    onAudioLevel: () => {},
    onSpeakerEvent: () => {},
    onError: () => {},
  }, recorder);
  const stoppedStart = stoppedClient.start();
  await Promise.resolve();
  socketHandlers.open();
  recorder.handlers.onStart();
  await stoppedStart;
  stoppedClient.stop();
  socketHandlers.close();

  assert.equal(stoppedStatuses.at(-1), "closed");

  const cancelledClient = new SpeakerTranscriptionClient({
    onStatus: () => {},
    onAudioLevel: () => {},
    onSpeakerEvent: () => {},
    onError: () => {},
  }, recorder);
  const cancelledStart = cancelledClient.start();
  await Promise.resolve();
  cancelledClient.close();
  await assert.rejects(cancelledStart, /连接已取消/);
});

test("speaker transcription client forwards provider diarization diagnostics", async () => {
  const socketHandlers = {};
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
        close() {},
      };
    },
  };
  const recorder = {
    owned: false,
    start() {
      this.owned = true;
      return true;
    },
    stop() {
      this.owned = false;
    },
    isOwnedBy() {
      return this.owned;
    },
  };
  const diagnostics = [];
  const { SpeakerTranscriptionClient } = speakerTranscriptionModuleWithSocketMocks();
  const client = new SpeakerTranscriptionClient({
    onStatus: () => {},
    onAudioLevel: () => {},
    onSpeakerEvent: () => {},
    onDiarizationStatus: (event) => diagnostics.push(event),
    onError: () => {},
  }, recorder);

  const started = client.start();
  await Promise.resolve();
  socketHandlers.open();
  await started;
  socketHandlers.message({
    data: JSON.stringify({
      type: "speaker.diarization",
      status: "missing_provider_labels",
      message: "当前 ASR 返回结果没有说话人标签。",
    }),
  });

  assert.equal(diagnostics.length, 1);
  assert.equal(diagnostics[0].status, "missing_provider_labels");
  assert.equal(diagnostics[0].message, "当前 ASR 返回结果没有说话人标签。");
  client.close();
});
