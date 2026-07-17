require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");


function resetModules() {
  for (const modulePath of [
    "../services/auth.ts",
    "../services/request.ts",
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

test("draft block API keeps only direct reading, editing, legacy unlock, and assembly", async () => {
  const requests = [];
  installWx(requests);
  resetModules();
  const {
    assembleMemoirChapterDraftBlocks,
    listMemoirDraftBlocks,
    manuallyReviseMemoirDraftBlock,
    unlockMemoirDraftBlock,
  } = require("../services/memoir-draft-block-api.ts");

  await listMemoirDraftBlocks("project/1", "chapter/1");
  await manuallyReviseMemoirDraftBlock("project/1", "block/1", {
    expected_current_version_id: "version-1",
    content: "修改后的正文",
  });
  await unlockMemoirDraftBlock("project/1", "block/1", {
    expected_current_version_id: "version-1",
    idempotency_key: "unlock-1",
  });
  await assembleMemoirChapterDraftBlocks("project/1", "chapter/1");

  const apiRequests = requests.filter((request) => request.url.includes("/api/memoir/projects/"));
  assert.match(apiRequests[0].url, /chapters\/chapter%2F1\/draft-blocks$/);
  assert.match(apiRequests[1].url, /draft-blocks\/block%2F1\/manual-revisions$/);
  assert.match(apiRequests[2].url, /draft-blocks\/block%2F1\/unlock$/);
  assert.match(apiRequests[3].url, /chapters\/chapter%2F1\/draft-blocks\/assemble$/);
  assert.doesNotMatch(JSON.stringify(apiRequests), /generation-jobs|revision-jobs|apply-revision/);
  delete global.wx;
});
