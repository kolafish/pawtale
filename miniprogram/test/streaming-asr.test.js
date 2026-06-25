require("./register-ts");

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
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

function streamingAsrProtocolModule() {
  delete require.cache[require.resolve("../services/memoir-streaming-asr-protocol.ts")];
  return require("../services/memoir-streaming-asr-protocol.ts");
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

function pcm16Frame(sample, byteLength = 3200) {
  const frame = new ArrayBuffer(byteLength);
  const view = new DataView(frame);
  for (let offset = 0; offset < byteLength; offset += 2) {
    view.setInt16(offset, sample, true);
  }
  return frame;
}

test("streaming ASR does not read constructor options from class field initializers", () => {
  const source = fs.readFileSync(
    path.join(__dirname, "../services/memoir-streaming-asr.ts"),
    "utf8",
  );
  assert.doesNotMatch(source, /private\s+voiceActivityDetector\s*=\s*this\.createVoiceActivityDetector\(\)/);
  assert.match(source, /private\s+voiceActivityDetector:\s*VoiceActivityDetector;/);

  const constructorIndex = source.indexOf("constructor(options: MemoirStreamingAsrOptions)");
  const assignOptionsIndex = source.indexOf("this.options = options;", constructorIndex);
  const createDetectorIndex = source.indexOf(
    "this.voiceActivityDetector = this.createVoiceActivityDetector();",
    constructorIndex,
  );
  assert.ok(constructorIndex > 0);
  assert.ok(assignOptionsIndex > constructorIndex);
  assert.ok(createDetectorIndex > assignOptionsIndex);
});

test("encodes ArrayBuffer audio frames as base64", () => {
  const { arrayBufferToBase64 } = streamingAsrProtocolModule();

  assert.equal(arrayBufferToBase64(Uint8Array.from([0, 1, 2, 253, 254, 255]).buffer), "AAEC/f7/");
  assert.equal(arrayBufferToBase64(Uint8Array.from([255]).buffer), "/w==");
  assert.equal(arrayBufferToBase64(Uint8Array.from([255, 238]).buffer), "/+4=");
});

test("estimates 16-bit PCM audio level", () => {
  const { estimatePcm16Level } = streamingAsrProtocolModule();
  const silent = new ArrayBuffer(4);
  const loud = new ArrayBuffer(4);
  const view = new DataView(loud);
  view.setInt16(0, 16384, true);
  view.setInt16(2, -16384, true);

  assert.equal(estimatePcm16Level(silent), 0);
  assert.ok(estimatePcm16Level(loud) > 0.49);
  assert.ok(estimatePcm16Level(loud) < 0.51);
});

test("voice activity detector can share logic with different pre-voice silence policy", () => {
  const { VoiceActivityDetector } = require("../services/voice-activity-detector.ts");
  const silent = new ArrayBuffer(3200);
  const searchVad = new VoiceActivityDetector({
    silenceLevelThreshold: 0.015,
    minVoiceActivityMs: 180,
  });
  const streamingVad = new VoiceActivityDetector({
    silenceLevelThreshold: 0.015,
    minVoiceActivityMs: 180,
    trackPreVoiceSilence: true,
  });

  assert.equal(searchVad.update(silent).silentElapsedMs, 0);
  assert.equal(streamingVad.update(silent).silentElapsedMs, 100);
});

test("normalizes invalid gateway payloads into session errors", () => {
  const {
    classifyVoiceClientConnectionError,
    classifyVoiceGatewayError,
    normalizeVoiceGatewayEvent,
  } = streamingAsrProtocolModule();

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
      message: "识别结果已返回，但保存讲述记录失败",
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
    classifyVoiceGatewayError({ type: "session.error", state: "failed", error: "voice_gateway_user_session_exists" }),
    {
      kind: "connection",
      code: "voice_gateway_user_session_exists",
      state: "failed",
      message: "已有语音连接正在收尾，请稍后重试",
    },
  );
  assert.deepEqual(
    classifyVoiceGatewayError({ type: "session.error", state: "failed", error: "voice_gateway_capacity_reached" }),
    {
      kind: "connection",
      code: "voice_gateway_capacity_reached",
      state: "failed",
      message: "当前语音服务繁忙，请稍后再试",
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
    classifyVoiceGatewayError({
      type: "session.error",
      state: "failed",
      error: "asr_provider_invalid_frame",
    }),
    {
      kind: "asr",
      code: "asr_provider_invalid_frame",
      state: "failed",
      message: "语音识别服务返回异常，请重试",
    },
  );
  assert.deepEqual(
    classifyVoiceGatewayError({
      type: "session.error",
      state: "failed",
      error: "Volc ASR frame payload is truncated",
    }),
    {
      kind: "asr",
      code: "asr_provider_invalid_frame",
      state: "failed",
      message: "语音识别服务返回异常，请重试",
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
  const { assistantTurnStreamUrl, voiceGatewayUrl } = require("../services/memoir-voice-api.ts");

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
  assert.equal(
    assistantTurnStreamUrl("voice/1"),
    "wss://api.example.com/api/mobile/memoir/voice-sessions/voice%2F1/assistant-turn-stream",
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
      if (options.method === "POST" && options.url.includes("/api/mobile/memoir/voice-sessions?")) {
        options.success({
          statusCode: 201,
          data: { session: { id: "voice/created" }, messages: [], utterances: [] },
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
  const {
    createMemoirVoiceSession,
    getMemoirVoiceProgress,
    listMemoirVoiceSessions,
    listMemoirVoiceTranscripts,
  } = require("../services/memoir-voice-api.ts");

  await createMemoirVoiceSession({ guidance_mode: "free_narration" }, { projectId: "project/1" });
  await listMemoirVoiceSessions({ projectId: "project/1", effectiveOnly: true, limit: 10, offset: 5 });
  await getMemoirVoiceProgress("project/1");
  await listMemoirVoiceTranscripts({
    projectId: "project/1",
    query: "外婆 家",
    included: "not_included",
    guidanceMode: "guided_interview",
    module: "place",
    limit: 10,
    offset: 5,
  });

  const apiRequests = requests.filter((request) => request.url.includes("/api/mobile/memoir/"));
  assert.equal(
    apiRequests[0].url,
    "http://backend.local:8000/api/mobile/memoir/voice-sessions?project_id=project%2F1",
  );
  assert.equal(apiRequests[0].method, "POST");
  assert.deepEqual(apiRequests[0].data, { guidance_mode: "free_narration" });
  assert.equal(
    apiRequests[1].url,
    "http://backend.local:8000/api/mobile/memoir/voice-sessions?project_id=project%2F1&effective_only=true&limit=10&offset=5",
  );
  assert.equal(apiRequests[2].url, "http://backend.local:8000/api/mobile/memoir/voice-progress?project_id=project%2F1");
  assert.equal(
    apiRequests[3].url,
    "http://backend.local:8000/api/mobile/memoir/voice-transcripts?project_id=project%2F1&q=%E5%A4%96%E5%A9%86%20%E5%AE%B6&included=not_included&guidance_mode=guided_interview&module=place&limit=10&offset=5",
  );
  assert.equal(apiRequests[0].header.Cookie, "pawtale_session=abc");
  assert.equal(apiRequests[1].header.Cookie, "pawtale_session=abc");
  assert.equal(apiRequests[2].header.Cookie, "pawtale_session=abc");
  assert.equal(apiRequests[3].header.Cookie, "pawtale_session=abc");
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

test("download voice audio explains missing downloadFile domain", async () => {
  global.wx = {
    getStorageSync(key) {
      if (key === "pawtale_backend_config") {
        return {
          httpBaseUrl: "https://pawtale-api.spaceflows.cn",
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
      options.success({ statusCode: 200, data: { id: "user-1" }, cookies: [], header: {} });
    },
    downloadFile(options) {
      assert.equal(
        options.url,
        "https://pawtale-api.spaceflows.cn/api/mobile/memoir/audio-files/audio%2F1/playback",
      );
      options.fail({ errMsg: "downloadFile:fail url not in domain list" });
    },
  };
  delete require.cache[require.resolve("../services/request.ts")];
  delete require.cache[require.resolve("../services/memoir-voice-api.ts")];
  const { downloadMemoirVoiceAudio } = require("../services/memoir-voice-api.ts");

  await assert.rejects(
    downloadMemoirVoiceAudio("audio/1"),
    /https:\/\/pawtale-api\.spaceflows\.cn.*downloadFile 合法域名/,
  );
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

test("streaming ASR connect times out while backend session check is pending", async () => {
  let socketCalls = 0;
  const statuses = [];
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
    request() {
      // Simulate a real-device request that never invokes success or fail.
    },
    connectSocket() {
      socketCalls += 1;
      return {
        onOpen() {},
        onMessage() {},
        onError() {},
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
    connectTimeoutMs: 20,
    callbacks: {
      onStatus(status) {
        statuses.push(status);
      },
      onEvent() {},
      onPartial() {},
      onFinal() {},
      onError() {},
    },
  });

  await assert.rejects(client.connect(), /语音连接超时/);

  assert.deepEqual(statuses, ["connecting"]);
  assert.equal(socketCalls, 0);
  delete global.wx;
});

test("streaming ASR ignores a socket that opens after connect timeout", async () => {
  let onOpen;
  let closeCalls = 0;
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
        onOpen(callback) {
          onOpen = callback;
        },
        onMessage() {},
        onError() {},
        onClose() {},
        send() {},
        close() {
          closeCalls += 1;
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
    connectTimeoutMs: 20,
    callbacks: {
      onStatus() {},
      onEvent() {},
      onPartial() {},
      onFinal() {},
      onError() {},
    },
  });

  await assert.rejects(client.connect(), /语音连接超时/);
  assert.equal(closeCalls, 1);

  onOpen();

  assert.equal(closeCalls, 2);
  assert.equal(client.startRecording(), false);
  delete global.wx;
});

test("streaming ASR starts provider only after recording starts", async () => {
  const recorder = recorderManagerHarness();
  const sent = [];
  const captureStats = [];
  const statuses = [];
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
      onStatus(status) {
        statuses.push(status);
      },
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

  assert.deepEqual(statuses, ["connecting", "ready"]);
  assert.deepEqual(sent, []);
  assert.equal(captureStats.at(-1).state, "idle");
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

test("streaming ASR maps recorder privacy ban to setup guidance", () => {
  const recorder = recorderManagerHarness();
  const statuses = [];
  let reportedMessage = "";
  global.wx = {
    getRecorderManager() {
      return recorder;
    },
  };
  delete require.cache[require.resolve("../services/memoir-streaming-asr.ts")];
  const { MemoirStreamingAsrClient } = require("../services/memoir-streaming-asr.ts");

  const client = new MemoirStreamingAsrClient({
    voiceSessionId: "voice-1",
    guidanceMode: "free_narration",
    callbacks: {
      onStatus(status) {
        statuses.push(status);
      },
      onEvent() {},
      onPartial() {},
      onFinal() {},
      onError(message) {
        reportedMessage = message;
      },
    },
  });

  client.handleRecorderError("operateRecorder:fail appid privacy api banned");

  assert.match(reportedMessage, /用户隐私保护指引.*麦克风\/录音用途声明/);
  assert.equal(statuses.at(-1), "error");
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

test("streaming ASR waits for voice before auto-stopping after silence", async () => {
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
  const voiceFrame = pcm16Frame(16384);
  recorder.callbacks.frame({ frameBuffer: silentFrame, isLastFrame: false });
  recorder.callbacks.frame({ frameBuffer: silentFrame, isLastFrame: false });
  assert.equal(recorder.stops, 0);

  recorder.callbacks.frame({ frameBuffer: voiceFrame, isLastFrame: false });
  recorder.callbacks.frame({ frameBuffer: voiceFrame, isLastFrame: false });
  recorder.callbacks.frame({ frameBuffer: voiceFrame, isLastFrame: false });
  recorder.callbacks.frame({ frameBuffer: silentFrame, isLastFrame: false });
  recorder.callbacks.frame({ frameBuffer: silentFrame, isLastFrame: false });

  assert.equal(recorder.stops, 1);
  assert.equal(captureStats.at(-1).state, "stopping");
  assert.equal(captureStats.at(-1).autoStopped, true);
  assert.equal(captureStats.at(-1).autoStopReason, "silence_after_voice");
  recorder.callbacks.stop();
  assert.deepEqual(sent.map((event) => event.type), [
    "session.start",
    "audio.frame",
    "audio.frame",
    "audio.frame",
    "audio.frame",
    "audio.frame",
    "audio.frame",
    "audio.frame",
    "audio.end",
  ]);
  delete global.wx;
});

test("streaming ASR auto-stops auto-listen when no voice answers", async () => {
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
  client.startRecording({ noVoiceActivityTimeoutMs: 200 });
  recorder.callbacks.start();
  const silentFrame = new ArrayBuffer(3200);
  recorder.callbacks.frame({ frameBuffer: silentFrame, isLastFrame: false });
  assert.equal(recorder.stops, 0);

  recorder.callbacks.frame({ frameBuffer: silentFrame, isLastFrame: false });

  assert.equal(recorder.stops, 1);
  assert.equal(captureStats.at(-1).state, "stopping");
  assert.equal(captureStats.at(-1).autoStopped, true);
  assert.equal(captureStats.at(-1).autoStopReason, "no_voice");
  recorder.callbacks.stop();
  assert.deepEqual(sent.map((event) => event.type), [
    "session.start",
    "audio.frame",
    "audio.frame",
    "audio.end",
  ]);
  delete global.wx;
});

test("streaming ASR no-answer timeout does not depend on silent frame callbacks", async () => {
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
  client.startRecording({ noVoiceActivityTimeoutMs: 20 });
  recorder.callbacks.start();

  await new Promise((resolve) => setTimeout(resolve, 30));

  assert.equal(recorder.stops, 1);
  assert.equal(captureStats.at(-1).state, "stopping");
  assert.equal(captureStats.at(-1).autoStopped, true);
  assert.equal(captureStats.at(-1).autoStopReason, "no_voice");
  assert.deepEqual(sent.map((event) => event.type), ["session.start"]);

  recorder.callbacks.stop();
  assert.deepEqual(sent.map((event) => event.type), ["session.start", "audio.end"]);
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
  assert.deepEqual(errors, ["识别结果已返回，但保存讲述记录失败"]);
  assert.deepEqual(errorInfos, [{
    kind: "persistence",
    code: "asr_final_persistence_failed",
    state: "failed",
    message: "识别结果已返回，但保存讲述记录失败",
  }]);
  assert.deepEqual(sent.map((event) => event.type), ["session.start", "audio.frame"]);
  assert.equal(captureStats.at(-1).state, "error");
  assert.equal(captureStats.at(-1).gatewayState, "failed");
  delete global.wx;
});
