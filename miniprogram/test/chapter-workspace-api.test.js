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

test("chapter completion API exposes workspace, section regeneration, prepare, and rewrite actions", async () => {
  const requests = [];
  installWx(requests);
  resetModules();
  const {
    getMemoirChapterWorkspace,
    regenerateMemoirChapterSections,
  } = require("../services/memoir-chapter-workspace-api.ts");
  const {
    prepareMemoirBookChapter,
    rewriteMemoirBookChapter,
  } = require("../services/memoir-book-completion-api.ts");

  await getMemoirChapterWorkspace("project/1", "chapter/1");
  await regenerateMemoirChapterSections(
    "project/1",
    "chapter/1",
    [{ id: "section/1", version: 3 }],
  );
  await prepareMemoirBookChapter("project/1", "chapter/1");
  await rewriteMemoirBookChapter("project/1", "chapter/1");

  const apiRequests = requests.filter((request) => request.url.includes("/api/memoir/projects/"));
  assert.equal(apiRequests[0].url, "http://backend.local:8000/api/memoir/projects/project%2F1/chapters/chapter%2F1/workspace");
  assert.equal(apiRequests[0].timeout, 10000);
  assert.equal(apiRequests[1].url, "http://backend.local:8000/api/memoir/projects/project%2F1/chapters/chapter%2F1/sections/regenerate");
  assert.deepEqual(apiRequests[1].data, {
    expected_sections: [{ id: "section/1", version: 3 }],
    ai_model: "deepseek-v4-flash",
  });
  assert.equal(apiRequests[1].timeout, 600000);
  assert.equal(apiRequests[2].url, "http://backend.local:8000/api/memoir/projects/project%2F1/book-completion/chapters/chapter%2F1/prepare");
  assert.deepEqual(apiRequests[2].data, { ai_model: "deepseek-v4-flash" });
  assert.equal(apiRequests[2].timeout, 600000);
  assert.equal(apiRequests[3].url, "http://backend.local:8000/api/memoir/projects/project%2F1/book-completion/chapters/chapter%2F1/rewrite");
  assert.deepEqual(apiRequests[3].data, { ai_model: "deepseek-v4-flash" });
  assert.equal(apiRequests[3].timeout, 600000);
  assert.equal(apiRequests.length, 4);
  delete global.wx;
});
