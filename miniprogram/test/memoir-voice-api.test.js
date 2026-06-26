require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

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

test("project progress APIs fetch snapshot and automation events", async () => {
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
      if (options.url.includes("/automation-events")) {
        options.success({
          statusCode: 200,
          data: {
            items: [
              {
                id: "event-1",
                project_id: "project/1",
                event_type: "source_assigned",
                status: "completed",
                utterance_ids: ["utterance-1"],
                summary_text: "已整理 1 条讲述。",
                metadata_json: {},
                created_at: "2026-06-23T12:00:00",
                updated_at: "2026-06-23T12:00:00",
              },
            ],
            total: 1,
            limit: 5,
          },
          cookies: [],
          header: {},
        });
        return;
      }
      options.success({
        statusCode: 200,
        data: {
          project_id: "project/1",
          valid_utterance_count: 2,
          assigned_utterance_count: 1,
          pending_utterance_count: 1,
          session_count: 1,
          active_session_count: 1,
          stopped_session_count: 0,
          failed_session_count: 0,
          user_message_count: 2,
          assistant_message_count: 1,
          source_char_count: 80,
          total_audio_seconds: 30,
          chapter_count: 1,
          confirmed_chapter_count: 1,
          draft_word_count: 120,
          recent_automation_events: [],
          chapter_progress_items: [],
        },
        cookies: [],
        header: {},
      });
    },
  };
  delete require.cache[require.resolve("../services/auth.ts")];
  delete require.cache[require.resolve("../services/request.ts")];
  delete require.cache[require.resolve("../services/memoir-voice-api.ts")];
  const {
    getMemoirProjectProgressSnapshot,
    listMemoirAutomationEvents,
  } = require("../services/memoir-voice-api.ts");

  const snapshot = await getMemoirProjectProgressSnapshot("project/1");
  const events = await listMemoirAutomationEvents("project/1", 5);

  const apiRequests = requests.filter((request) => request.url.includes("/api/mobile/memoir/"));
  assert.equal(
    apiRequests[0].url,
    "http://backend.local:8000/api/mobile/memoir/projects/project%2F1/progress-snapshot",
  );
  assert.equal(
    apiRequests[1].url,
    "http://backend.local:8000/api/mobile/memoir/projects/project%2F1/automation-events?limit=5",
  );
  assert.equal(snapshot.assigned_utterance_count, 1);
  assert.equal(events.items[0].summary_text, "已整理 1 条讲述。");
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
