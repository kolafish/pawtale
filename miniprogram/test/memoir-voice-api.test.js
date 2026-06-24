require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

test("voice follow-up API includes the selected AI model", async () => {
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
      if (key === "pawtale_ai_model_settings") {
        return { model: "deepseek-v4-flash" };
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
      options.success({
        statusCode: 200,
        data: {
          message: { id: "message-1" },
          follow_up_text: "你愿意再讲讲这件事吗？",
        },
        cookies: [],
        header: {},
      });
    },
  };
  delete require.cache[require.resolve("../services/auth.ts")];
  delete require.cache[require.resolve("../services/request.ts")];
  delete require.cache[require.resolve("../services/ai-model-settings.ts")];
  delete require.cache[require.resolve("../services/memoir-voice-api.ts")];
  const { createMemoirVoiceFollowUp } = require("../services/memoir-voice-api.ts");

  await createMemoirVoiceFollowUp("voice/1", { regenerate: true });

  const apiRequests = requests.filter((request) => request.url.includes("/api/mobile/memoir/"));
  assert.equal(
    apiRequests[0].url,
    "http://backend.local:8000/api/mobile/memoir/voice-sessions/voice%2F1/follow-up",
  );
  assert.equal(apiRequests[0].method, "POST");
  assert.deepEqual(apiRequests[0].data, {
    regenerate: true,
    ai_model: "deepseek-v4-flash",
  });
  delete global.wx;
});

test("voice turn plan API includes the selected AI model", async () => {
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
      if (key === "pawtale_ai_model_settings") {
        return { model: "doubao-seed-2-0-pro-260215" };
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
      options.success({
        statusCode: 200,
        data: {
          plan: {
            user_signal: "memory_detail",
            response_strategy: "direct_follow",
            should_play_ack: false,
            acknowledgement_text: "",
            next_question_brief: "追问时间地点",
          },
          acknowledgement: null,
        },
        cookies: [],
        header: {},
      });
    },
  };
  delete require.cache[require.resolve("../services/auth.ts")];
  delete require.cache[require.resolve("../services/request.ts")];
  delete require.cache[require.resolve("../services/ai-model-settings.ts")];
  delete require.cache[require.resolve("../services/memoir-voice-api.ts")];
  const { createMemoirVoiceTurnPlan } = require("../services/memoir-voice-api.ts");

  await createMemoirVoiceTurnPlan("voice/1", {});

  const apiRequests = requests.filter((request) => request.url.includes("/api/mobile/memoir/"));
  assert.equal(
    apiRequests[0].url,
    "http://backend.local:8000/api/mobile/memoir/voice-sessions/voice%2F1/turn-plan",
  );
  assert.equal(apiRequests[0].method, "POST");
  assert.deepEqual(apiRequests[0].data, {
    ai_model: "doubao-seed-2-0-pro-260215",
  });
  delete global.wx;
});

test("voice no-answer prompt API posts attempt without selected AI model", async () => {
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
      if (key === "pawtale_ai_model_settings") {
        return { model: "deepseek-v4-flash" };
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
      options.success({
        statusCode: 200,
        data: {
          message: { id: "message-1" },
          follow_up_text: "我这边没有听到声音，您可以接着刚才的问题慢慢说。",
        },
        cookies: [],
        header: {},
      });
    },
  };
  delete require.cache[require.resolve("../services/auth.ts")];
  delete require.cache[require.resolve("../services/request.ts")];
  delete require.cache[require.resolve("../services/ai-model-settings.ts")];
  delete require.cache[require.resolve("../services/memoir-voice-api.ts")];
  const { createMemoirVoiceNoAnswerPrompt } = require("../services/memoir-voice-api.ts");

  await createMemoirVoiceNoAnswerPrompt("voice/1", { attempt: 1 });

  const apiRequests = requests.filter((request) => request.url.includes("/api/mobile/memoir/"));
  assert.equal(
    apiRequests[0].url,
    "http://backend.local:8000/api/mobile/memoir/voice-sessions/voice%2F1/no-answer-prompt",
  );
  assert.equal(apiRequests[0].method, "POST");
  assert.deepEqual(apiRequests[0].data, { attempt: 1 });
  delete global.wx;
});

test("voice search transcription API posts recorded audio", async () => {
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
      options.success({
        statusCode: 200,
        data: { transcript: "外婆家的院子" },
        cookies: [],
        header: {},
      });
    },
  };
  delete require.cache[require.resolve("../services/auth.ts")];
  delete require.cache[require.resolve("../services/request.ts")];
  delete require.cache[require.resolve("../services/memoir-voice-api.ts")];
  const { transcribeMemoirVoiceSearchAudio } = require("../services/memoir-voice-api.ts");

  const response = await transcribeMemoirVoiceSearchAudio("cGNt", "audio/pcm");

  const apiRequests = requests.filter((request) => request.url.includes("/api/mobile/memoir/"));
  assert.equal(apiRequests[0].url, "http://backend.local:8000/api/mobile/memoir/search-query/transcribe");
  assert.equal(apiRequests[0].method, "POST");
  assert.deepEqual(apiRequests[0].data, {
    audio_base64: "cGNt",
    mime_type: "audio/pcm",
  });
  assert.equal(apiRequests[0].header.Cookie, "pawtale_session=abc");
  assert.deepEqual(response, { transcript: "外婆家的院子" });
  delete global.wx;
});

test("voice search query parse API posts text and selected AI model", async () => {
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
      options.success({
        statusCode: 200,
        data: {
          original_text: "帮我找外婆家院子",
          query: "外婆 院子",
          keywords: ["外婆", "院子"],
          filters: { included: "not_included", guidance_mode: null, module: "place" },
          display_text: "外婆 院子 · 未收录 · 地点",
          used_ai: true,
          provider: "fake-ai",
          model: "deepseek",
        },
        cookies: [],
        header: {},
      });
    },
  };
  delete require.cache[require.resolve("../services/auth.ts")];
  delete require.cache[require.resolve("../services/request.ts")];
  delete require.cache[require.resolve("../services/memoir-voice-api.ts")];
  const { parseMemoirVoiceSearchQuery } = require("../services/memoir-voice-api.ts");

  const response = await parseMemoirVoiceSearchQuery("帮我找外婆家院子", "deepseek");

  const apiRequests = requests.filter((request) => request.url.includes("/api/mobile/memoir/"));
  assert.equal(apiRequests[0].url, "http://backend.local:8000/api/mobile/memoir/search-query/parse");
  assert.equal(apiRequests[0].method, "POST");
  assert.deepEqual(apiRequests[0].data, {
    text: "帮我找外婆家院子",
    ai_model: "deepseek",
  });
  assert.equal(apiRequests[0].header.Cookie, "pawtale_session=abc");
  assert.equal(response.query, "外婆 院子");
  assert.equal(response.filters.module, "place");
  delete global.wx;
});
