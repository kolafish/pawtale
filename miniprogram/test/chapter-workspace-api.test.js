require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");


function resetModules() {
  for (const modulePath of [
    "../services/auth.ts",
    "../services/request.ts",
    "../services/ai-model-settings.ts",
    "../services/memoir-api-client.ts",
    "../services/memoir-book-completion-api.ts",
    "../services/memoir-chapter-workspace-api.ts",
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
      if (key === "pawtale_ai_model_settings") {
        return { model: "deepseek-v4-flash" };
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

test("chapter completion API exposes only workspace, prepare, and fact confirmation actions", async () => {
  const requests = [];
  installWx(requests);
  resetModules();
  const {
    getMemoirChapterWorkspace,
    confirmMemoirChapterFact,
  } = require("../services/memoir-chapter-workspace-api.ts");
  const {
    prepareMemoirBookChapter,
  } = require("../services/memoir-book-completion-api.ts");

  await getMemoirChapterWorkspace("project/1", "chapter/1");
  await prepareMemoirBookChapter("project/1", "chapter/1");
  await confirmMemoirChapterFact("project/1", "gap/1", "正确的是十八岁");

  const apiRequests = requests.filter((request) => request.url.includes("/api/memoir/projects/"));
  assert.equal(apiRequests[0].url, "http://backend.local:8000/api/memoir/projects/project%2F1/chapters/chapter%2F1/workspace");
  assert.equal(apiRequests[1].url, "http://backend.local:8000/api/memoir/projects/project%2F1/book-completion/chapters/chapter%2F1/prepare");
  assert.deepEqual(apiRequests[1].data, {});
  assert.equal(apiRequests[2].url, "http://backend.local:8000/api/memoir/projects/project%2F1/gaps/gap%2F1/resolve");
  assert.equal(apiRequests[2].data.idempotency_key, "chapter-confirmation:gap/1");
  assert.equal(apiRequests[2].data.note, "正确的是十八岁");
  delete global.wx;
});
