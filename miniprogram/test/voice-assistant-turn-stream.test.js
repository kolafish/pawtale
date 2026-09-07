require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

function installWxSocketHarness() {
  const handlers = {};
  const sent = [];
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
  };
  return { handlers, sent, closeCalls: () => closeCalls };
}

function socketMessage(handlers, payload) {
  handlers.message({ data: JSON.stringify(payload) });
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

test("assistant turn stream sends pcm frame protocol and emits audio frames", async () => {
  const harness = installWxSocketHarness();
  delete require.cache[require.resolve("../services/request.ts")];
  delete require.cache[require.resolve("../services/ai-model-settings.ts")];
  delete require.cache[require.resolve("../services/tts-settings.ts")];
  delete require.cache[require.resolve("../services/memoir-voice-session-api.ts")];
  delete require.cache[require.resolve("../services/voice-assistant-turn-stream.ts")];
  const { VoiceAssistantTurnStreamClient } = require("../services/voice-assistant-turn-stream.ts");
  const formats = [];
  const frames = [];
  const doneEvents = [];
  const client = new VoiceAssistantTurnStreamClient("voice/1", {
    onAudioFormat: (event) => formats.push(event),
    onAudioFrame: (frame) => frames.push(frame),
    onAudioDone: (event) => doneEvents.push(event),
  });

  const startPromise = client.start();
  await new Promise((resolve) => setImmediate(resolve));
  harness.handlers.open();
  socketMessage(harness.handlers, { type: "turn.ready" });
  socketMessage(harness.handlers, {
    type: "audio.format",
    turn_id: "turn-1",
    voice_session_id: "voice/1",
    sequence: 1,
    mime_type: "audio/L16",
    sample_rate: 16000,
    channels: 1,
    bits_per_sample: 16,
  });
  socketMessage(harness.handlers, {
    type: "audio.frame",
    turn_id: "turn-1",
    voice_session_id: "voice/1",
    sequence: 1,
    audio: Buffer.from("pcm-frame").toString("base64"),
    mime_type: "audio/L16",
    sample_rate: 16000,
    channels: 1,
    bits_per_sample: 16,
  });
  socketMessage(harness.handlers, {
    type: "audio.done",
    turn_id: "turn-1",
    voice_session_id: "voice/1",
    frame_count: 1,
    duration_seconds: 0.2,
    sample_rate: 16000,
    channels: 1,
    bits_per_sample: 16,
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
  await startPromise;

  assert.deepEqual(harness.sent, [{
    type: "turn.start",
    ai_model: "deepseek-v4-pro",
    regenerate: false,
    voice: "zh_female_vv_uranus_bigtts",
    speed_ratio: 1.5,
    audio_protocol: "pcm_frame",
  }]);
  assert.equal(formats.length, 1);
  assert.equal(frames.length, 1);
  assert.equal(Buffer.from(frames[0].audio).toString(), "pcm-frame");
  assert.equal(frames[0].mimeType, "audio/L16");
  assert.equal(frames[0].sampleRate, 16000);
  assert.equal(frames[0].channels, 1);
  assert.equal(frames[0].bitsPerSample, 16);
  assert.equal(doneEvents[0].frame_count, 1);
  delete global.wx;
});

test("assistant turn stream rejects when socket does not open before connect timeout", async () => {
  const harness = installWxSocketHarness();
  delete require.cache[require.resolve("../services/request.ts")];
  delete require.cache[require.resolve("../services/voice-assistant-turn-stream.ts")];
  const { VoiceAssistantTurnStreamClient } = require("../services/voice-assistant-turn-stream.ts");
  const client = new VoiceAssistantTurnStreamClient("voice-1");

  const startPromise = assert.rejects(client.start({ connectTimeoutMs: 5 }), /AI 回应连接超时/);
  await new Promise((resolve) => setImmediate(resolve));

  await startPromise;
  assert.equal(harness.closeCalls(), 1);
  delete global.wx;
});

test("assistant turn stream connect timeout stops after socket opens", async (context) => {
  context.mock.timers.enable({ apis: ["setTimeout"] });
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

  context.mock.timers.tick(20);
  await Promise.resolve();
  assert.equal(rejected, undefined);
  assert.equal(resolved, undefined);
  assert.deepEqual(harness.sent, [{
    type: "turn.start",
    ai_model: "deepseek-v4-pro",
    regenerate: false,
    voice: "zh_female_vv_uranus_bigtts",
    speed_ratio: 1.5,
    audio_protocol: "pcm_frame",
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

test("assistant turn stream rejects when opened stream has no activity", async () => {
  const harness = installWxSocketHarness();
  delete require.cache[require.resolve("../services/request.ts")];
  delete require.cache[require.resolve("../services/voice-assistant-turn-stream.ts")];
  const { VoiceAssistantTurnStreamClient } = require("../services/voice-assistant-turn-stream.ts");
  const client = new VoiceAssistantTurnStreamClient("voice-1");

  const startPromise = assert.rejects(
    client.start({ connectTimeoutMs: 50, turnActivityTimeoutMs: 10 }),
    /AI 回应等待超时/,
  );
  await new Promise((resolve) => setImmediate(resolve));
  harness.handlers.open();

  await startPromise;
  assert.equal(harness.closeCalls(), 1);
  delete global.wx;
});

test("assistant turn stream activity timeout resets on valid events", async () => {
  const harness = installWxSocketHarness();
  delete require.cache[require.resolve("../services/request.ts")];
  delete require.cache[require.resolve("../services/voice-assistant-turn-stream.ts")];
  const { VoiceAssistantTurnStreamClient } = require("../services/voice-assistant-turn-stream.ts");
  const client = new VoiceAssistantTurnStreamClient("voice-1");

  const startPromise = client.start({ connectTimeoutMs: 50, turnActivityTimeoutMs: 100 });
  await new Promise((resolve) => setImmediate(resolve));
  harness.handlers.open();
  socketMessage(harness.handlers, { type: "turn.ready" });
  await sleep(20);
  socketMessage(harness.handlers, {
    type: "text.delta",
    turn_id: "turn-1",
    voice_session_id: "voice-1",
    text_delta: "还记得",
    text: "还记得",
  });
  await sleep(20);
  socketMessage(harness.handlers, {
    type: "turn.completed",
    turn_id: "turn-1",
    voice_session_id: "voice-1",
    state: "completed",
    message_id: "assistant-1",
    text: "还记得那天谁也在场吗？",
    primary_intent: "follow_up",
    intent_tags: ["follow_up"],
    expects_answer: true,
  });

  const completed = await startPromise;
  assert.equal(completed.message_id, "assistant-1");
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
  harness.handlers.open();
  socketMessage(harness.handlers, { type: "turn.ready" });
  await new Promise((resolve) => setImmediate(resolve));
  client.cancel("interrupted_by_user");

  await assert.rejects(startPromise, /AI 回应已取消/);
  assert.deepEqual(harness.sent[1], {
    type: "turn.cancel",
    reason: "interrupted_by_user",
  });
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

test("assistant turn stream closes socket on socket errors", async () => {
  const harness = installWxSocketHarness();
  delete require.cache[require.resolve("../services/request.ts")];
  delete require.cache[require.resolve("../services/voice-assistant-turn-stream.ts")];
  const { VoiceAssistantTurnStreamClient } = require("../services/voice-assistant-turn-stream.ts");
  const client = new VoiceAssistantTurnStreamClient("voice-1");

  const startPromise = client.start({ connectTimeoutMs: 50, turnActivityTimeoutMs: 50 });
  await new Promise((resolve) => setImmediate(resolve));
  harness.handlers.open();
  harness.handlers.error({ errMsg: "socket broken" });

  await assert.rejects(startPromise, /socket broken/);
  assert.equal(harness.closeCalls(), 1);
  delete global.wx;
});
