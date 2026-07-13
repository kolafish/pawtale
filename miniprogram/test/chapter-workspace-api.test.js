require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");


function resetModules() {
  for (const modulePath of [
    "../services/auth.ts",
    "../services/request.ts",
    "../services/ai-model-settings.ts",
    "../services/memoir-api-client.ts",
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

test("chapter workspace API uses narrative resource routes and explicit action payloads", async () => {
  const requests = [];
  installWx(requests);
  resetModules();
  const {
    confirmMemoirChapterContract,
    decideMemoirClaim,
    decideMemoirStory,
    enqueueMemoirChapterWorkspaceAudit,
    getMemoirChapterWorkspace,
    resolveMemoirNarrativeGap,
    resolveMemoirStorySlot,
  } = require("../services/memoir-chapter-workspace-api.ts");

  await getMemoirChapterWorkspace("project/1", "chapter/1");
  await enqueueMemoirChapterWorkspaceAudit("project/1", "chapter/1");
  await resolveMemoirNarrativeGap("project/1", "gap/1", "not-applicable", {
    idempotency_key: "decision-1",
    note: "该项不适用于本章",
  });
  await decideMemoirClaim("project/1", "claim/1", "confirm", {
    idempotency_key: "claim-decision-1",
    reason: "证据已核对",
  });
  await decideMemoirStory("project/1", "story/1", "reject", {
    idempotency_key: "story-decision-1",
    reason: "不用于成书",
  });
  await resolveMemoirStorySlot(
    "project/1",
    "story/1",
    "time/place",
    "accepted_incomplete",
    {
      idempotency_key: "slot-decision-1",
      note: "叙述者暂时无法补充",
    },
  );
  await confirmMemoirChapterContract("project/1", "contract/1");

  const apiRequests = requests.filter((request) => request.url.includes("/api/memoir/projects/"));
  assert.equal(apiRequests[0].url, "http://backend.local:8000/api/memoir/projects/project%2F1/chapters/chapter%2F1/workspace");
  assert.equal(apiRequests[1].url, "http://backend.local:8000/api/memoir/projects/project%2F1/chapters/chapter%2F1/workspace/audits");
  assert.deepEqual(apiRequests[1].data, {
    include_semantic_audit: true,
    ai_model: "deepseek-v4-flash",
  });
  assert.equal(apiRequests[2].url, "http://backend.local:8000/api/memoir/projects/project%2F1/gaps/gap%2F1/not-applicable");
  assert.deepEqual(apiRequests[2].data, {
    idempotency_key: "decision-1",
    note: "该项不适用于本章",
  });
  assert.equal(apiRequests[3].url, "http://backend.local:8000/api/memoir/projects/project%2F1/claims/claim%2F1/confirm");
  assert.deepEqual(apiRequests[3].data, {
    idempotency_key: "claim-decision-1",
    reason: "证据已核对",
  });
  assert.equal(apiRequests[4].url, "http://backend.local:8000/api/memoir/projects/project%2F1/stories/story%2F1/reject");
  assert.deepEqual(apiRequests[4].data, {
    idempotency_key: "story-decision-1",
    reason: "不用于成书",
  });
  assert.equal(apiRequests[5].url, "http://backend.local:8000/api/memoir/projects/project%2F1/stories/story%2F1/slots/time%2Fplace/resolve");
  assert.deepEqual(apiRequests[5].data, {
    idempotency_key: "slot-decision-1",
    note: "叙述者暂时无法补充",
    status: "accepted_incomplete",
  });
  assert.equal(apiRequests[6].url, "http://backend.local:8000/api/memoir/projects/project%2F1/chapter-contracts/contract%2F1/confirm");
  assert.deepEqual(apiRequests[6].data, {});
  delete global.wx;
});
