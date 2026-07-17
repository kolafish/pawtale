require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

function resetModules() {
  for (const modulePath of [
    "../services/auth.ts",
    "../services/request.ts",
    "../services/ai-model-settings.ts",
    "../services/memoir-api-client.ts",
    "../services/memoir-completion-item-api.ts",
  ]) {
    delete require.cache[require.resolve(modulePath)];
  }
}

function installWx(requests) {
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
    getExtConfigSync() { return {}; },
    setStorageSync() {},
    removeStorageSync() {},
    request(options) {
      requests.push(options);
      if (options.url.endsWith("/api/auth/me")) {
        options.success({ statusCode: 200, data: { id: "user-1" }, cookies: [], header: {} });
        return;
      }
      options.success({ statusCode: 200, data: {}, cookies: [], header: {} });
    },
  };
}

test("completion item API uses project-scoped routes and compact payloads", async () => {
  const requests = [];
  installWx(requests);
  resetModules();
  const {
    deferMemoirCompletionItem,
    getMemoirCompletionItem,
    markMemoirCompletionItemUnableToAnswer,
    resolveMemoirCompletionItem,
    switchMemoirCompletionItem,
  } = require("../services/memoir-completion-item-api.ts");

  await getMemoirCompletionItem("project/1", "task/1");
  await deferMemoirCompletionItem("project/1", "task/1", {
    note: "以后再补",
  });
  await markMemoirCompletionItemUnableToAnswer("project/1", "task/1", {
    note: "暂时答不上来",
  });
  await resolveMemoirCompletionItem("project/1", "task/1", {
    note: "已经处理",
  });
  await switchMemoirCompletionItem("project/1", "voice/1", {
    current_item_id: "task/1",
    note: "换一个问题",
  });

  const apiRequests = requests.filter((request) => request.url.includes("/api/memoir/projects/"));
  assert.equal(apiRequests[0].url, "http://backend.local:8000/api/memoir/projects/project%2F1/completion-items/task%2F1");
  assert.deepEqual(apiRequests[1].data, { note: "以后再补" });
  assert.deepEqual(apiRequests[2].data, { note: "暂时答不上来" });
  assert.equal(apiRequests[2].url, "http://backend.local:8000/api/memoir/projects/project%2F1/completion-items/task%2F1/unable-to-answer");
  assert.deepEqual(apiRequests[3].data, { note: "已经处理" });
  assert.equal(apiRequests[3].url, "http://backend.local:8000/api/memoir/projects/project%2F1/completion-items/task%2F1/resolve");
  assert.equal(apiRequests[4].url, "http://backend.local:8000/api/memoir/projects/project%2F1/voice-sessions/voice%2F1/completion-item/switch");
  assert.deepEqual(apiRequests[4].data, {
    current_item_id: "task/1",
    note: "换一个问题",
  });
  delete global.wx;
});
