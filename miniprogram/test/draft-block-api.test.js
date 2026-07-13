require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");


function resetModules() {
  for (const modulePath of [
    "../services/auth.ts",
    "../services/request.ts",
    "../services/ai-model-settings.ts",
    "../services/memoir-api-client.ts",
    "../services/memoir-draft-block-api.ts",
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

test("draft block API uses scoped resources and sends selected AI model only to generation jobs", async () => {
  const requests = [];
  installWx(requests);
  resetModules();
  const {
    assembleMemoirChapterDraftBlocks,
    enqueueMemoirDraftBlockGeneration,
    enqueueMemoirDraftBlockRevision,
    listMemoirDraftBlocks,
  } = require("../services/memoir-draft-block-api.ts");

  await listMemoirDraftBlocks("project/1", "chapter/1");
  await enqueueMemoirDraftBlockGeneration("project/1", "chapter/1", { node_ids: ["node/1"] });
  await enqueueMemoirDraftBlockRevision("project/1", "block/1", {
    expected_current_version_id: "version-1",
    feedback: "只修改这一段",
  });
  await assembleMemoirChapterDraftBlocks("project/1", "chapter/1");

  const apiRequests = requests.filter((request) => request.url.includes("/api/memoir/projects/"));
  assert.equal(apiRequests[0].url, "http://backend.local:8000/api/memoir/projects/project%2F1/chapters/chapter%2F1/draft-blocks");
  assert.equal(apiRequests[1].url, "http://backend.local:8000/api/memoir/projects/project%2F1/chapters/chapter%2F1/draft-blocks/generation-jobs");
  assert.deepEqual(apiRequests[1].data, { node_ids: ["node/1"], ai_model: "deepseek-v4-flash" });
  assert.equal(apiRequests[2].url, "http://backend.local:8000/api/memoir/projects/project%2F1/draft-blocks/block%2F1/revision-jobs");
  assert.deepEqual(apiRequests[2].data, {
    expected_current_version_id: "version-1",
    feedback: "只修改这一段",
    ai_model: "deepseek-v4-flash",
  });
  assert.equal(apiRequests[3].url, "http://backend.local:8000/api/memoir/projects/project%2F1/chapters/chapter%2F1/draft-blocks/assemble");
  delete global.wx;
});
