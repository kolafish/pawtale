require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

function resetModules() {
  for (const modulePath of [
    "../services/auth.ts",
    "../services/request.ts",
    "../services/ai-model-settings.ts",
    "../services/memoir-api-client.ts",
    "../services/memoir-completion-issue-api.ts",
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
        options.success({
          statusCode: 200,
          data: { id: "user-1" },
          cookies: [],
          header: {},
        });
        return;
      }
      options.success({ statusCode: 200, data: {}, cookies: [], header: {} });
    },
  };
}

test("completion issue API exposes one collection and one close action", async () => {
  const requests = [];
  installWx(requests);
  resetModules();
  const {
    closeMemoirCompletionIssue,
    listMemoirCompletionIssues,
  } = require("../services/memoir-completion-issue-api.ts");

  await listMemoirCompletionIssues("project/1", {
    chapterId: "chapter/1",
    includeClosed: true,
  });
  await closeMemoirCompletionIssue("project/1", "issue/1", {
    outcome: "deferred",
    note: "以后再补",
  });

  const apiRequests = requests.filter((request) => (
    request.url.includes("/api/memoir/projects/")
  ));
  assert.equal(
    apiRequests[0].url,
    "http://backend.local:8000/api/memoir/projects/project%2F1/completion-issues?chapter_id=chapter%2F1&include_closed=true",
  );
  assert.equal(
    apiRequests[1].url,
    "http://backend.local:8000/api/memoir/projects/project%2F1/completion-issues/issue%2F1/close",
  );
  assert.deepEqual(apiRequests[1].data, {
    outcome: "deferred",
    note: "以后再补",
  });
  assert.equal(apiRequests.length, 2);
  delete global.wx;
});
