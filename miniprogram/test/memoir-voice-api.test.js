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
