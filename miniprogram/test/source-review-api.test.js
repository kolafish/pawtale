require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");


function resetModules() {
  for (const modulePath of [
    "../services/auth.ts",
    "../services/request.ts",
    "../services/ai-model-settings.ts",
    "../services/memoir-api-client.ts",
    "../services/memoir-book-source-review-api.ts",
  ]) {
    delete require.cache[require.resolve(modulePath)];
  }
}


test("source review API uses mobile project routes and explicit answer parts", async () => {
  const requests = [];
  global.wx = {
    getStorageSync(key) {
      if (key === "pawtale_backend_config") {
        return { httpBaseUrl: "http://backend.local:8000/" };
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
  resetModules();
  const {
    confirmMemoirBookSourceReviewItem,
    listMemoirBookSourceReviewItems,
  } = require("../services/memoir-book-source-review-api.ts");

  await listMemoirBookSourceReviewItems("project/1");
  await confirmMemoirBookSourceReviewItem("project/1", "event/1", {
    followup_answer_kind: "clarification",
    clarification_text: "只用于解释原文",
  });

  const apiRequests = requests.filter((request) => request.url.includes("book-source-review-items"));
  assert.equal(
    apiRequests[0].url,
    "http://backend.local:8000/api/mobile/memoir/projects/project%2F1/book-source-review-items",
  );
  assert.equal(
    apiRequests[1].url,
    "http://backend.local:8000/api/mobile/memoir/projects/project%2F1/book-source-review-items/event%2F1/confirm",
  );
  assert.equal(apiRequests[1].method, "POST");
  assert.deepEqual(apiRequests[1].data, {
    followup_answer_kind: "clarification",
    clarification_text: "只用于解释原文",
  });
  delete global.wx;
});
