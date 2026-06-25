require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

function installWxSocketHarness() {
  const handlers = {};
  const sent = [];
  const writes = [];
  const unlinks = [];
  let closeCalls = 0;
  global.wx = {
    env: { USER_DATA_PATH: "/tmp/pawtale" },
    getStorageSync(key) {
      if (key === "pawtale_backend_config") {
        return {
          httpBaseUrl: "http://backend.local:8000",
          wsBaseUrl: "ws://backend.local:8000",
          devEmail: "local@example.com",
          devPassword: "secret",
        };
      }
      if (key === "pawtale_backend_cookie") {
        return "pawtale_session=abc";
      }
      if (key === "pawtale_ai_model_settings") {
        return { model: "deepseek-v4-pro" };
      }
      if (key === "pawtale_memoir_tts_settings") {
        return {
          chat: { voice: "zh_female_vv_uranus_bigtts", speedRatio: 1.5 },
          book: { voice: "", speedRatio: 1 },
        };
      }
      return "";
    },
    getExtConfigSync() {
      return {};
    },
    setStorageSync() {},
    removeStorageSync() {},
    request(options) {
      options.success({ statusCode: 200, data: { id: "user-1" }, cookies: [], header: {} });
    },
    connectSocket(options) {
      handlers.connectOptions = options;
      return {
        onOpen(callback) {
          handlers.open = callback;
        },
        onMessage(callback) {
          handlers.message = callback;
        },
        onError(callback) {
          handlers.error = callback;
        },
        onClose(callback) {
          handlers.close = callback;
        },
        send(options) {
          sent.push(JSON.parse(options.data));
        },
        close() {
          closeCalls += 1;
        },
      };
    },
    base64ToArrayBuffer(value) {
      const buffer = Buffer.from(value, "base64");
      return buffer.buffer.slice(buffer.byteOffset, buffer.byteOffset + buffer.byteLength);
    },
    getFileSystemManager() {
      return {
        writeFile(options) {
          writes.push({
            filePath: options.filePath,
            data: Buffer.from(options.data),
          });
          options.success?.({ errMsg: "writeFile:ok" });
        },
        unlink(options) {
          unlinks.push(options.filePath);
          options.success?.({ errMsg: "unlink:ok" });
        },
      };
    },
  };
  return { handlers, sent, writes, unlinks, closeCalls: () => closeCalls };
}

function socketMessage(handlers, payload) {
  handlers.message({ data: JSON.stringify(payload) });
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

test("assistant turn stream sends start payload and emits playable wav segments", async () => {
  const harness = installWxSocketHarness();
  delete require.cache[require.resolve("../services/request.ts")];
  delete require.cache[require.resolve("../services/ai-model-settings.ts")];
  delete require.cache[require.resolve("../services/tts-settings.ts")];
  delete require.cache[require.resolve("../services/memoir-voice-api.ts")];
  delete require.cache[require.resolve("../services/voice-assistant-turn-stream.ts")];
  const { VoiceAssistantTurnStreamClient } = require("../services/voice-assistant-turn-stream.ts");
  const segments = [];
  const completedEvents = [];
  const client = new VoiceAssistantTurnStreamClient("voice/1", {
    onAudioSegment: (segment) => segments.push(segment),
    onCompleted: (event) => completedEvents.push(event),
  });

  const startPromise = client.start();
  await new Promise((resolve) => setImmediate(resolve));
  harness.handlers.open();
  socketMessage(harness.handlers, { type: "turn.ready" });
  socketMessage(harness.handlers, {
    type: "text.segment",
    turn_id: "turn-1",
    voice_session_id: "voice/1",
    segment_index: 0,
    text: "那时候在院子里，谁最常陪您？",
  });
  socketMessage(harness.handlers, {
    type: "audio.chunk",
    turn_id: "turn-1",
    voice_session_id: "voice/1",
    segment_index: 0,
    sequence: 1,
    audio: Buffer.from("pcm").toString("base64"),
    mime_type: "audio/L16",
  });
  socketMessage(harness.handlers, {
    type: "audio.segment_done",
    turn_id: "turn-1",
    voice_session_id: "voice/1",
    segment_index: 0,
    duration_seconds: 0.2,
  });
  socketMessage(harness.handlers, {
    type: "turn.completed",
    turn_id: "turn-1",
    voice_session_id: "voice/1",
    state: "completed",
    message_id: "assistant-1",
    audio_file_id: "audio-1",
    text: "那时候在院子里，谁最常陪您？",
    primary_intent: "follow_up",
    intent_tags: ["follow_up"],
    expects_answer: true,
  });
  const completed = await startPromise;

  assert.equal(
    harness.handlers.connectOptions.url,
    "ws://backend.local:8000/api/mobile/memoir/voice-sessions/voice%2F1/assistant-turn-stream",
  );
  assert.deepEqual(harness.sent, [{
    type: "turn.start",
    ai_model: "deepseek-v4-pro",
    regenerate: false,
    voice: "zh_female_vv_uranus_bigtts",
    speed_ratio: 1.5,
  }]);
  assert.equal(segments.length, 1);
  assert.equal(segments[0].text, "那时候在院子里，谁最常陪您？");
  assert.equal(segments[0].tempFilePath, "/tmp/pawtale/assistant-turn-turn-1-0.wav");
  assert.equal(harness.writes[0].filePath, segments[0].tempFilePath);
  assert.equal(harness.writes[0].data.subarray(0, 4).toString("ascii"), "RIFF");
  assert.equal(harness.writes[0].data.subarray(8, 12).toString("ascii"), "WAVE");
  assert.equal(harness.writes[0].data.subarray(44).toString(), "pcm");
  assert.equal(completed.message_id, "assistant-1");
  assert.equal(completedEvents[0].message_id, "assistant-1");
  assert.equal(harness.closeCalls(), 1);
  segments[0].dispose();
  assert.deepEqual(harness.unlinks, [segments[0].tempFilePath]);
  client.disposeTempFiles();
  assert.deepEqual(harness.unlinks, [segments[0].tempFilePath]);
  delete global.wx;
});

test("assistant turn stream rejects when socket does not open before connect timeout", async () => {
  const harness = installWxSocketHarness();
  delete require.cache[require.resolve("../services/request.ts")];
  delete require.cache[require.resolve("../services/voice-assistant-turn-stream.ts")];
  const { VoiceAssistantTurnStreamClient } = require("../services/voice-assistant-turn-stream.ts");
  const client = new VoiceAssistantTurnStreamClient("voice-1");

  const startPromise = client.start({ connectTimeoutMs: 5 });
  await new Promise((resolve) => setImmediate(resolve));

  await assert.rejects(startPromise, /AI 回应连接超时/);
  assert.equal(harness.closeCalls(), 1);
  delete global.wx;
});

test("assistant turn stream connect timeout stops after socket opens", async () => {
  const harness = installWxSocketHarness();
  delete require.cache[require.resolve("../services/request.ts")];
  delete require.cache[require.resolve("../services/voice-assistant-turn-stream.ts")];
  const { VoiceAssistantTurnStreamClient } = require("../services/voice-assistant-turn-stream.ts");
  const client = new VoiceAssistantTurnStreamClient("voice-1");
  let resolved;
  let rejected;

  const observed = client.start({ connectTimeoutMs: 5 }).then(
    (event) => {
      resolved = event;
    },
    (error) => {
      rejected = error;
    },
  );
  await new Promise((resolve) => setImmediate(resolve));
  harness.handlers.open();
  socketMessage(harness.handlers, { type: "turn.ready" });

  await sleep(20);
  assert.equal(rejected, undefined);
  assert.equal(resolved, undefined);
  assert.deepEqual(harness.sent, [{
    type: "turn.start",
    ai_model: "deepseek-v4-pro",
    regenerate: false,
    voice: "zh_female_vv_uranus_bigtts",
    speed_ratio: 1.5,
  }]);

  socketMessage(harness.handlers, {
    type: "turn.completed",
    turn_id: "turn-1",
    voice_session_id: "voice-1",
    state: "completed",
    message_id: "assistant-1",
    text: "已连接后不应该被连接超时打断。",
    primary_intent: "follow_up",
    intent_tags: ["follow_up"],
    expects_answer: true,
  });
  await observed;

  assert.equal(rejected, undefined);
  assert.equal(resolved.message_id, "assistant-1");
  delete global.wx;
});

test("assistant turn stream local cancel settles pending start", async () => {
  const harness = installWxSocketHarness();
  delete require.cache[require.resolve("../services/request.ts")];
  delete require.cache[require.resolve("../services/voice-assistant-turn-stream.ts")];
  const { VoiceAssistantTurnStreamClient } = require("../services/voice-assistant-turn-stream.ts");
  const client = new VoiceAssistantTurnStreamClient("voice-1");

  const startPromise = client.start({ connectTimeoutMs: 50 });
  await new Promise((resolve) => setImmediate(resolve));
  client.cancel();

  await assert.rejects(startPromise, /AI 回应已取消/);
  assert.equal(harness.closeCalls(), 1);
  delete global.wx;
});

test("assistant turn stream rejects failed turn events", async () => {
  const harness = installWxSocketHarness();
  delete require.cache[require.resolve("../services/request.ts")];
  delete require.cache[require.resolve("../services/voice-assistant-turn-stream.ts")];
  const { VoiceAssistantTurnStreamClient } = require("../services/voice-assistant-turn-stream.ts");
  const failed = [];
  const client = new VoiceAssistantTurnStreamClient("voice-1", {
    onFailed: (event) => failed.push(event),
  });

  const startPromise = client.start();
  await new Promise((resolve) => setImmediate(resolve));
  harness.handlers.open();
  socketMessage(harness.handlers, { type: "turn.ready" });
  socketMessage(harness.handlers, {
    type: "turn.failed",
    turn_id: "turn-1",
    error: "provider failed",
  });

  await assert.rejects(startPromise, /provider failed/);
  assert.equal(failed[0].error, "provider failed");
  delete global.wx;
});
