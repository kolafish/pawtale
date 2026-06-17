require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

function streamingAsrModule() {
  global.wx = {
    getRecorderManager() {
      return {
        start() {},
        stop() {},
        onStart() {},
        onFrameRecorded() {},
        onError() {},
        onStop() {},
      };
    },
  };
  delete require.cache[require.resolve("../services/memoir-streaming-asr.ts")];
  return require("../services/memoir-streaming-asr.ts");
}

function recorderManagerStub() {
  return {
    start() {},
    stop() {},
    onStart() {},
    onFrameRecorded() {},
    onError() {},
    onStop() {},
  };
}

function recorderManagerHarness() {
  const callbacks = {};
  return {
    starts: [],
    stops: 0,
    callbacks,
    start(options) {
      this.starts.push(options);
    },
    stop() {
      this.stops += 1;
    },
    onStart(callback) {
      callbacks.start = callback;
    },
    onFrameRecorded(callback) {
      callbacks.frame = callback;
    },
    onError(callback) {
      callbacks.error = callback;
    },
    onStop(callback) {
      callbacks.stop = callback;
    },
  };
}

test("encodes ArrayBuffer audio frames as base64", () => {
  const { arrayBufferToBase64 } = streamingAsrModule();

  assert.equal(arrayBufferToBase64(Uint8Array.from([0, 1, 2, 253, 254, 255]).buffer), "AAEC/f7/");
  assert.equal(arrayBufferToBase64(Uint8Array.from([255]).buffer), "/w==");
  assert.equal(arrayBufferToBase64(Uint8Array.from([255, 238]).buffer), "/+4=");

  delete global.wx;
});

test("estimates 16-bit PCM audio level", () => {
  const { estimatePcm16Level } = streamingAsrModule();
  const silent = new ArrayBuffer(4);
  const loud = new ArrayBuffer(4);
  const view = new DataView(loud);
  view.setInt16(0, 16384, true);
  view.setInt16(2, -16384, true);

  assert.equal(estimatePcm16Level(silent), 0);
  assert.ok(estimatePcm16Level(loud) > 0.49);
  assert.ok(estimatePcm16Level(loud) < 0.51);

  delete global.wx;
});

test("normalizes invalid gateway payloads into session errors", () => {
  const {
    classifyVoiceClientConnectionError,
    classifyVoiceGatewayError,
    normalizeVoiceGatewayEvent,
  } = streamingAsrModule();

  assert.deepEqual(normalizeVoiceGatewayEvent("bad"), {
    type: "session.error",
    error: "invalid_gateway_event",
  });
  assert.deepEqual(normalizeVoiceGatewayEvent({ text: "missing type" }), {
    type: "session.error",
    text: "missing type",
  });
  assert.deepEqual(
    classifyVoiceGatewayError({
      type: "session.error",
      state: "failed",
      error: "asr_final_persistence_failed",
    }),
    {
      kind: "persistence",
      code: "asr_final_persistence_failed",
      state: "failed",
      message: "转写已返回，但保存识别结果失败",
    },
  );
  assert.equal(
    classifyVoiceGatewayError({ type: "session.error", error: "asr_session_not_started" }).kind,
    "gateway",
  );
  assert.deepEqual(
    classifyVoiceGatewayError({ type: "session.error", error: "audio_stream_too_large" }),
    {
      kind: "gateway",
      code: "audio_stream_too_large",
      state: undefined,
      message: "本次录音超过可保存大小",
    },
  );
  assert.deepEqual(
    classifyVoiceGatewayError({ type: "session.error", state: "failed", error: "voice_gateway_auth_failed" }),
    {
      kind: "connection",
      code: "voice_gateway_auth_failed",
      state: "failed",
      message: "登录状态已失效，请重新登录后再进入",
    },
  );
  assert.deepEqual(
    classifyVoiceGatewayError({
      type: "session.error",
      state: "failed",
      error: "asr_provider_resource_not_granted",
    }),
    {
      kind: "asr",
      code: "asr_provider_resource_not_granted",
      state: "failed",
      message: "ASR 资源未开通或 API Key 无权限",
    },
  );
  assert.deepEqual(
    classifyVoiceClientConnectionError("server rejected WebSocket connection: HTTP 403"),
    {
      kind: "connection",
      code: "voice_gateway_connection_forbidden",
      message: "语音连接被拒绝，请返回后重新进入",
    },
  );

  delete global.wx;
});

test("normalizes set-cookie values before reusing them as Cookie headers", () => {
  const { cookieHeaderFromSetCookie } = require("../services/request.ts");

  assert.equal(
    cookieHeaderFromSetCookie(
      ["pawtale_session=abc; Path=/; HttpOnly; SameSite=lax"],
      { "set-cookie": "another=value; Path=/" },
    ),
    "pawtale_session=abc; another=value",
  );
});

test("builds voice gateway URLs from runtime backend config", () => {
  global.wx = {
    getStorageSync(key) {
      if (key === "pawtale_backend_config") {
        return {
          httpBaseUrl: "https://api.example.com/",
          devEmail: "local@example.com",
          devPassword: "secret",
        };
      }
      return "";
    },
    getExtConfigSync() {
      return {};
    },
    setStorageSync() {},
  };
  delete require.cache[require.resolve("../services/request.ts")];
  delete require.cache[require.resolve("../services/memoir-voice-api.ts")];
  const { readBackendConfig } = require("../services/request.ts");
  const { voiceGatewayUrl } = require("../services/memoir-voice-api.ts");

  assert.deepEqual(readBackendConfig(), {
    httpBaseUrl: "https://api.example.com/",
    wsBaseUrl: "wss://api.example.com/",
    devEmail: "local@example.com",
    devPassword: "secret",
    devDisplayName: "本地作者",
  });
  assert.equal(
    voiceGatewayUrl("voice/1"),
    "wss://api.example.com/api/mobile/memoir/voice-sessions/voice%2F1/gateway",
  );

  delete global.wx;
});

test("fetches voice session detail through backend API", async () => {
  const requests = [];
  const detail = {
    session: {
      id: "voice/1",
      project_id: "project-1",
      status: "stopped",
      guidance_mode: "free_narration",
      error_message: "",
      metadata_json: {},
      created_at: "2026-06-08T00:00:00.000Z",
      updated_at: "2026-06-08T00:00:00.000Z",
    },
    messages: [],
    utterances: [],
  };
  global.wx = {
    getStorageSync(key) {
      if (key === "pawtale_backend_config") {
        return {
          httpBaseUrl: "http://backend.local:8000/",
          devEmail: "local@example.com",
          devPassword: "secret",
        };
      }
      if (key === "pawtale_backend_cookie") {
        return "pawtale_session=abc";
      }
      return "";
    },
    getExtConfigSync() {
      return {};
    },
    setStorageSync() {},
    removeStorageSync() {},
    request(options) {
      requests.push(options);
      if (options.url.endsWith("/api/auth/me")) {
        options.success({ statusCode: 200, data: { id: "user-1" }, cookies: [], header: {} });
        return;
      }
      options.success({ statusCode: 200, data: detail, cookies: [], header: {} });
    },
  };
  delete require.cache[require.resolve("../services/request.ts")];
  delete require.cache[require.resolve("../services/memoir-voice-api.ts")];
  const { getMemoirVoiceSession } = require("../services/memoir-voice-api.ts");

  const result = await getMemoirVoiceSession("voice/1");

  assert.deepEqual(result, detail);
  assert.equal(requests.length, 2);
  assert.equal(requests[1].url, "http://backend.local:8000/api/mobile/memoir/voice-sessions/voice%2F1");
  assert.equal(requests[1].method, "GET");
  assert.equal(requests[1].header.Cookie, "pawtale_session=abc");
  delete global.wx;
});

test("fetches voice session list and progress through backend API", async () => {
  const requests = [];
  global.wx = {
    getStorageSync(key) {
      if (key === "pawtale_backend_config") {
        return {
          httpBaseUrl: "http://backend.local:8000/",
          devEmail: "local@example.com",
          devPassword: "secret",
        };
      }
      if (key === "pawtale_backend_cookie") {
        return "pawtale_session=abc";
      }
      return "";
    },
    getExtConfigSync() {
      return {};
    },
    setStorageSync() {},
    removeStorageSync() {},
    request(options) {
      requests.push(options);
      if (options.url.endsWith("/api/auth/me")) {
        options.success({ statusCode: 200, data: { id: "user-1" }, cookies: [], header: {} });
        return;
      }
      if (options.url.includes("/api/mobile/memoir/voice-progress")) {
        options.success({
          statusCode: 200,
          data: {
            session_count: 0,
            active_session_count: 0,
            stopped_session_count: 0,
            failed_session_count: 0,
            utterance_count: 0,
            user_message_count: 0,
            assistant_message_count: 0,
            total_user_char_count: 0,
            total_audio_seconds: 0,
            modules: [],
          },
          cookies: [],
          header: {},
        });
        return;
      }
      options.success({ statusCode: 200, data: { items: [], total: 0, limit: 10, offset: 0 }, cookies: [], header: {} });
    },
  };
  delete require.cache[require.resolve("../services/request.ts")];
  delete require.cache[require.resolve("../services/memoir-voice-api.ts")];
  const { getMemoirVoiceProgress, listMemoirVoiceSessions } = require("../services/memoir-voice-api.ts");

  await listMemoirVoiceSessions({ projectId: "project/1", limit: 10, offset: 5 });
  await getMemoirVoiceProgress("project/1");

  const apiRequests = requests.filter((request) => request.url.includes("/api/mobile/memoir/"));
  assert.equal(
    apiRequests[0].url,
    "http://backend.local:8000/api/mobile/memoir/voice-sessions?project_id=project%2F1&limit=10&offset=5",
  );
  assert.equal(apiRequests[1].url, "http://backend.local:8000/api/mobile/memoir/voice-progress?project_id=project%2F1");
  assert.equal(apiRequests[0].header.Cookie, "pawtale_session=abc");
  assert.equal(apiRequests[1].header.Cookie, "pawtale_session=abc");
  delete global.wx;
});

test("updates and deletes voice transcripts through backend API", async () => {
  const requests = [];
  const updated = {
    id: "utterance/1",
    voice_session_id: "voice-1",
    project_id: "project-1",
    project_title: "我的回忆录",
    transcript_raw: "原文",
    transcript_edited: "校正文",
    display_text: "校正文",
    raw_char_count: 2,
    edited_char_count: 3,
    display_char_count: 3,
    session_status: "stopped",
    guidance_mode: "free_narration",
    created_at: "2026-06-08T00:00:00.000Z",
    session_created_at: "2026-06-08T00:00:00.000Z",
  };
  global.wx = {
    getStorageSync(key) {
      if (key === "pawtale_backend_config") {
        return {
          httpBaseUrl: "http://backend.local:8000/",
          devEmail: "local@example.com",
          devPassword: "secret",
        };
      }
      if (key === "pawtale_backend_cookie") {
        return "pawtale_session=abc";
      }
      return "";
    },
    getExtConfigSync() {
      return {};
    },
    setStorageSync() {},
    removeStorageSync() {},
    request(options) {
      requests.push(options);
      if (options.url.endsWith("/api/auth/me")) {
        options.success({ statusCode: 200, data: { id: "user-1" }, cookies: [], header: {} });
        return;
      }
      if (options.method === "DELETE") {
        options.success({ statusCode: 204, data: "", cookies: [], header: {} });
        return;
      }
      options.success({ statusCode: 200, data: updated, cookies: [], header: {} });
    },
  };
  delete require.cache[require.resolve("../services/request.ts")];
  delete require.cache[require.resolve("../services/memoir-voice-api.ts")];
  const {
    deleteMemoirVoiceTranscript,
    updateMemoirVoiceTranscript,
  } = require("../services/memoir-voice-api.ts");

  const result = await updateMemoirVoiceTranscript("utterance/1", "校正文");
  await deleteMemoirVoiceTranscript("utterance/1");

  const apiRequests = requests.filter((request) => request.url.includes("/api/mobile/memoir/voice-transcripts"));
  assert.deepEqual(result, updated);
  assert.equal(apiRequests[0].url, "http://backend.local:8000/api/mobile/memoir/voice-transcripts/utterance%2F1");
  assert.equal(apiRequests[0].method, "PATCH");
  assert.deepEqual(apiRequests[0].data, { transcript_edited: "校正文" });
  assert.equal(apiRequests[1].url, "http://backend.local:8000/api/mobile/memoir/voice-transcripts/utterance%2F1");
  assert.equal(apiRequests[1].method, "DELETE");
  assert.equal(apiRequests[1].header.Cookie, "pawtale_session=abc");
  delete global.wx;
});

test("streaming ASR connect rejects on socket error before open", async () => {
  let onError;
  global.wx = {
    getRecorderManager: recorderManagerStub,
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
      return "";
    },
    setStorageSync() {},
    removeStorageSync() {},
    request(options) {
      options.success({ statusCode: 200, data: { id: "user-1" }, cookies: [], header: {} });
    },
    connectSocket() {
      return {
        onOpen() {},
        onMessage() {},
        onError(callback) {
          onError = callback;
        },
        onClose() {},
        send() {},
        close() {},
      };
    },
  };
  delete require.cache[require.resolve("../services/request.ts")];
  delete require.cache[require.resolve("../services/memoir-voice-api.ts")];
  delete require.cache[require.resolve("../services/memoir-streaming-asr.ts")];
  const { MemoirStreamingAsrClient } = require("../services/memoir-streaming-asr.ts");
  const client = new MemoirStreamingAsrClient({
    voiceSessionId: "voice-1",
    guidanceMode: "free_narration",
    callbacks: {
      onStatus() {},
      onEvent() {},
      onPartial() {},
      onFinal() {},
      onError() {},
    },
  });

  const connectPromise = client.connect();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(typeof onError, "function");
  onError({ errMsg: "socket failed" });

  await assert.rejects(connectPromise, /语音连接失败，请重新连接/);
  delete global.wx;
});

test("streaming ASR starts provider only after recording starts", async () => {
  const recorder = recorderManagerHarness();
  const sent = [];
  const captureStats = [];
  let onOpen;
  global.wx = {
    getRecorderManager() {
      return recorder;
    },
    getStorageSync(key) {
      if (key === "pawtale_backend_config") {
        return {
          httpBaseUrl: "http://backend.local:8000",
          wsBaseUrl: "ws://backend.local:8000",
        };
      }
      if (key === "pawtale_backend_cookie") {
        return "pawtale_session=abc";
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
    connectSocket() {
      return {
        onOpen(callback) {
          onOpen = callback;
        },
        onMessage() {},
        onError() {},
        onClose() {},
        send(options) {
          sent.push(JSON.parse(options.data));
        },
        close() {},
      };
    },
  };
  delete require.cache[require.resolve("../services/request.ts")];
  delete require.cache[require.resolve("../services/memoir-voice-api.ts")];
  delete require.cache[require.resolve("../services/memoir-streaming-asr.ts")];
  const { MemoirStreamingAsrClient } = require("../services/memoir-streaming-asr.ts");
  const client = new MemoirStreamingAsrClient({
    voiceSessionId: "voice-1",
    guidanceMode: "free_narration",
    callbacks: {
      onStatus() {},
      onCaptureStats(stats) {
        captureStats.push(stats);
      },
      onEvent() {},
      onPartial() {},
      onFinal() {},
      onError() {},
    },
  });

  const connectPromise = client.connect();
  await new Promise((resolve) => setImmediate(resolve));
  onOpen({ errMsg: "connect:ok" });
  await connectPromise;

  assert.deepEqual(sent, []);
  assert.equal(client.startRecording(), true);
  assert.deepEqual(sent, []);
  assert.equal(recorder.starts.length, 1);
  assert.equal(recorder.starts[0].sampleRate, 16000);
  assert.equal(recorder.starts[0].encodeBitRate, 96000);
  assert.equal(captureStats.at(-1).state, "opening");

  recorder.callbacks.start();

  assert.equal(sent.length, 1);
  assert.equal(sent[0].type, "session.start");
  assert.equal(captureStats.at(-1).state, "recording");
  delete global.wx;
});

test("streaming ASR stop before recorder start does not call recorder stop", async () => {
  const recorder = recorderManagerHarness();
  const sent = [];
  let onOpen;
  global.wx = {
    getRecorderManager() {
      return recorder;
    },
    getStorageSync(key) {
      if (key === "pawtale_backend_config") {
        return {
          httpBaseUrl: "http://backend.local:8000",
          wsBaseUrl: "ws://backend.local:8000",
        };
      }
      if (key === "pawtale_backend_cookie") {
        return "pawtale_session=abc";
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
    connectSocket() {
      return {
        onOpen(callback) {
          onOpen = callback;
        },
        onMessage() {},
        onError() {},
        onClose() {},
        send(options) {
          sent.push(JSON.parse(options.data));
        },
        close() {},
      };
    },
  };
  delete require.cache[require.resolve("../services/request.ts")];
  delete require.cache[require.resolve("../services/memoir-voice-api.ts")];
  delete require.cache[require.resolve("../services/memoir-streaming-asr.ts")];
  const { MemoirStreamingAsrClient } = require("../services/memoir-streaming-asr.ts");
  const client = new MemoirStreamingAsrClient({
    voiceSessionId: "voice-1",
    guidanceMode: "free_narration",
    callbacks: {
      onStatus() {},
      onEvent() {},
      onPartial() {},
      onFinal() {},
      onError() {},
    },
  });

  const connectPromise = client.connect();
  await new Promise((resolve) => setImmediate(resolve));
  onOpen({ errMsg: "connect:ok" });
  await connectPromise;
  client.startRecording();
  client.stopRecording();

  assert.equal(recorder.stops, 0);
  assert.deepEqual(sent.map((event) => event.type), []);
  delete global.wx;
});

test("streaming ASR reports captured audio frame stats", async () => {
  const recorder = recorderManagerHarness();
  const sent = [];
  const captureStats = [];
  let onOpen;
  global.wx = {
    getRecorderManager() {
      return recorder;
    },
    getStorageSync(key) {
      if (key === "pawtale_backend_config") {
        return {
          httpBaseUrl: "http://backend.local:8000",
          wsBaseUrl: "ws://backend.local:8000",
        };
      }
      if (key === "pawtale_backend_cookie") {
        return "pawtale_session=abc";
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
    connectSocket() {
      return {
        onOpen(callback) {
          onOpen = callback;
        },
        onMessage() {},
        onError() {},
        onClose() {},
        send(options) {
          sent.push(JSON.parse(options.data));
        },
        close() {},
      };
    },
  };
  delete require.cache[require.resolve("../services/request.ts")];
  delete require.cache[require.resolve("../services/memoir-voice-api.ts")];
  delete require.cache[require.resolve("../services/memoir-streaming-asr.ts")];
  const { MemoirStreamingAsrClient } = require("../services/memoir-streaming-asr.ts");
  const client = new MemoirStreamingAsrClient({
    voiceSessionId: "voice-1",
    guidanceMode: "free_narration",
    callbacks: {
      onStatus() {},
      onCaptureStats(stats) {
        captureStats.push(stats);
      },
      onEvent() {},
      onPartial() {},
      onFinal() {},
      onError() {},
    },
  });

  const connectPromise = client.connect();
  await new Promise((resolve) => setImmediate(resolve));
  onOpen({ errMsg: "connect:ok" });
  await connectPromise;
  client.startRecording();
  recorder.callbacks.start();
  recorder.callbacks.frame({
    frameBuffer: Uint8Array.from([1, 2, 3, 4]).buffer,
    isLastFrame: false,
  });
  recorder.callbacks.frame({
    frameBuffer: Uint8Array.from([5, 6]).buffer,
    isLastFrame: false,
  });

  assert.deepEqual(sent.map((event) => event.type), ["session.start", "audio.frame", "audio.frame"]);
  assert.equal(captureStats.at(-1).state, "recording");
  assert.equal(captureStats.at(-1).frameCount, 2);
  assert.equal(captureStats.at(-1).audioBytesSent, 6);
  assert.equal(typeof captureStats.at(-1).recordingElapsedMs, "number");
  delete global.wx;
});

test("streaming ASR auto-stops after configured silence", async () => {
  const recorder = recorderManagerHarness();
  const sent = [];
  const captureStats = [];
  let onOpen;
  global.wx = {
    getRecorderManager() {
      return recorder;
    },
    getStorageSync(key) {
      if (key === "pawtale_backend_config") {
        return {
          httpBaseUrl: "http://backend.local:8000",
          wsBaseUrl: "ws://backend.local:8000",
        };
      }
      if (key === "pawtale_backend_cookie") {
        return "pawtale_session=abc";
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
    connectSocket() {
      return {
        onOpen(callback) {
          onOpen = callback;
        },
        onMessage() {},
        onError() {},
        onClose() {},
        send(options) {
          sent.push(JSON.parse(options.data));
        },
        close() {},
      };
    },
  };
  delete require.cache[require.resolve("../services/request.ts")];
  delete require.cache[require.resolve("../services/memoir-voice-api.ts")];
  delete require.cache[require.resolve("../services/memoir-streaming-asr.ts")];
  const { MemoirStreamingAsrClient } = require("../services/memoir-streaming-asr.ts");
  const client = new MemoirStreamingAsrClient({
    voiceSessionId: "voice-1",
    guidanceMode: "free_narration",
    autoStopSilenceMs: 200,
    minAutoStopRecordingMs: 0,
    callbacks: {
      onStatus() {},
      onCaptureStats(stats) {
        captureStats.push(stats);
      },
      onEvent() {},
      onPartial() {},
      onFinal() {},
      onError() {},
    },
  });

  const connectPromise = client.connect();
  await new Promise((resolve) => setImmediate(resolve));
  onOpen({ errMsg: "connect:ok" });
  await connectPromise;
  client.startRecording();
  recorder.callbacks.start();
  const silentFrame = new ArrayBuffer(3200);
  recorder.callbacks.frame({ frameBuffer: silentFrame, isLastFrame: false });
  recorder.callbacks.frame({ frameBuffer: silentFrame, isLastFrame: false });

  assert.equal(recorder.stops, 1);
  assert.equal(captureStats.at(-1).state, "stopping");
  assert.equal(captureStats.at(-1).autoStopped, true);
  recorder.callbacks.stop();
  assert.deepEqual(sent.map((event) => event.type), [
    "session.start",
    "audio.frame",
    "audio.frame",
    "audio.end",
  ]);
  delete global.wx;
});

test("streaming ASR close drains active audio before closing socket", async () => {
  const recorder = recorderManagerHarness();
  const sent = [];
  const closes = [];
  let onOpen;
  let onMessage;
  global.wx = {
    getRecorderManager() {
      return recorder;
    },
    getStorageSync(key) {
      if (key === "pawtale_backend_config") {
        return {
          httpBaseUrl: "http://backend.local:8000",
          wsBaseUrl: "ws://backend.local:8000",
        };
      }
      if (key === "pawtale_backend_cookie") {
        return "pawtale_session=abc";
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
    connectSocket() {
      return {
        onOpen(callback) {
          onOpen = callback;
        },
        onMessage(callback) {
          onMessage = callback;
        },
        onError() {},
        onClose() {},
        send(options) {
          sent.push(JSON.parse(options.data));
        },
        close(options) {
          closes.push(options);
        },
      };
    },
  };
  delete require.cache[require.resolve("../services/request.ts")];
  delete require.cache[require.resolve("../services/memoir-voice-api.ts")];
  delete require.cache[require.resolve("../services/memoir-streaming-asr.ts")];
  const { MemoirStreamingAsrClient } = require("../services/memoir-streaming-asr.ts");
  const client = new MemoirStreamingAsrClient({
    voiceSessionId: "voice-1",
    guidanceMode: "free_narration",
    callbacks: {
      onStatus() {},
      onEvent() {},
      onPartial() {},
      onFinal() {},
      onError() {},
    },
  });

  const connectPromise = client.connect();
  await new Promise((resolve) => setImmediate(resolve));
  onOpen({ errMsg: "connect:ok" });
  await connectPromise;
  client.startRecording();
  recorder.callbacks.start();
  recorder.callbacks.frame({
    frameBuffer: Uint8Array.from([1, 2, 3, 4]).buffer,
    isLastFrame: false,
  });

  client.close();

  assert.equal(recorder.stops, 1);
  assert.deepEqual(sent.map((event) => event.type), ["session.start", "audio.frame"]);
  assert.equal(closes.length, 0);

  recorder.callbacks.stop();
  assert.deepEqual(sent.map((event) => event.type), ["session.start", "audio.frame", "audio.end"]);

  onMessage({ data: JSON.stringify({ type: "audio.ended", state: "ready" }) });
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(sent.map((event) => event.type), [
    "session.start",
    "audio.frame",
    "audio.end",
    "session.stop",
  ]);
  assert.equal(closes.length, 0);

  onMessage({ data: JSON.stringify({ type: "session.stopped", state: "stopped" }) });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(closes.length, 1);
  delete global.wx;
});

test("streaming ASR stops recorder when gateway reports an error", async () => {
  const recorder = recorderManagerHarness();
  const sent = [];
  const errors = [];
  const errorInfos = [];
  const captureStats = [];
  let onOpen;
  let onMessage;
  global.wx = {
    getRecorderManager() {
      return recorder;
    },
    getStorageSync(key) {
      if (key === "pawtale_backend_config") {
        return {
          httpBaseUrl: "http://backend.local:8000",
          wsBaseUrl: "ws://backend.local:8000",
        };
      }
      if (key === "pawtale_backend_cookie") {
        return "pawtale_session=abc";
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
    connectSocket() {
      return {
        onOpen(callback) {
          onOpen = callback;
        },
        onMessage(callback) {
          onMessage = callback;
        },
        onError() {},
        onClose() {},
        send(options) {
          sent.push(JSON.parse(options.data));
        },
        close() {},
      };
    },
  };
  delete require.cache[require.resolve("../services/request.ts")];
  delete require.cache[require.resolve("../services/memoir-voice-api.ts")];
  delete require.cache[require.resolve("../services/memoir-streaming-asr.ts")];
  const { MemoirStreamingAsrClient } = require("../services/memoir-streaming-asr.ts");
  const client = new MemoirStreamingAsrClient({
    voiceSessionId: "voice-1",
    guidanceMode: "free_narration",
    callbacks: {
      onStatus() {},
      onCaptureStats(stats) {
        captureStats.push(stats);
      },
      onEvent() {},
      onPartial() {},
      onFinal() {},
      onError(message, errorInfo) {
        errors.push(message);
        errorInfos.push(errorInfo);
      },
    },
  });

  const connectPromise = client.connect();
  await new Promise((resolve) => setImmediate(resolve));
  onOpen({ errMsg: "connect:ok" });
  await connectPromise;
  client.startRecording();
  recorder.callbacks.start();
  recorder.callbacks.frame({
    frameBuffer: Uint8Array.from([1, 2, 3, 4]).buffer,
    isLastFrame: false,
  });

  onMessage({
    data: JSON.stringify({
      type: "session.error",
      state: "failed",
      error: "asr_final_persistence_failed",
    }),
  });
  recorder.callbacks.frame({
    frameBuffer: Uint8Array.from([5, 6]).buffer,
    isLastFrame: false,
  });

  assert.equal(recorder.stops, 1);
  assert.deepEqual(errors, ["转写已返回，但保存识别结果失败"]);
  assert.deepEqual(errorInfos, [{
    kind: "persistence",
    code: "asr_final_persistence_failed",
    state: "failed",
    message: "转写已返回，但保存识别结果失败",
  }]);
  assert.deepEqual(sent.map((event) => event.type), ["session.start", "audio.frame"]);
  assert.equal(captureStats.at(-1).state, "error");
  assert.equal(captureStats.at(-1).gatewayState, "failed");
  delete global.wx;
});
