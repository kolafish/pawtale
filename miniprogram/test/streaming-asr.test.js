require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

function streamingAsrModule() {
  global.wx = {
    getRecorderManager() {
      return {
        start() {},
        stop() {},
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
    onFrameRecorded() {},
    onError() {},
    onStop() {},
  };
}

test("encodes ArrayBuffer audio frames as base64", () => {
  const { arrayBufferToBase64 } = streamingAsrModule();

  assert.equal(arrayBufferToBase64(Uint8Array.from([0, 1, 2, 253, 254, 255]).buffer), "AAEC/f7/");
  assert.equal(arrayBufferToBase64(Uint8Array.from([255]).buffer), "/w==");
  assert.equal(arrayBufferToBase64(Uint8Array.from([255, 238]).buffer), "/+4=");

  delete global.wx;
});

test("normalizes invalid gateway payloads into session errors", () => {
  const { normalizeVoiceGatewayEvent } = streamingAsrModule();

  assert.deepEqual(normalizeVoiceGatewayEvent("bad"), {
    type: "session.error",
    error: "invalid_gateway_event",
  });
  assert.deepEqual(normalizeVoiceGatewayEvent({ text: "missing type" }), {
    type: "session.error",
    text: "missing type",
  });

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

  await assert.rejects(connectPromise, /socket failed/);
  delete global.wx;
});
