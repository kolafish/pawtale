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
    createMemoirChapterContractRevision,
    enqueueMemoirChapterWorkspaceAudit,
    getMemoirChapterWorkspace,
    includeMemoirStoryInChapter,
    resolveMemoirNarrativeGap,
  } = require("../services/memoir-chapter-workspace-api.ts");

  await getMemoirChapterWorkspace("project/1", "chapter/1");
  await createMemoirChapterContractRevision("project/1", "chapter/1", {
    idempotency_key: "revision-1",
    objective: "讲清离家经历。",
    reader_takeaway: "理解离家的影响。",
    time_scope_text: "青年时期",
    time_start: null,
    time_end: null,
    core_questions: [{ key: "meaning", question: "为什么重要？" }],
    constraints: {
      include_story_ids: [],
      exclude_story_ids: [],
      include_topics: [],
      exclude_topics: [],
    },
    copy_from_contract_id: "contract/1",
    copy_nodes: true,
  });
  await includeMemoirStoryInChapter("project/1", "contract/1", "story/1", {
    idempotency_key: "include-1",
    reason: "属于本章",
  });
  await enqueueMemoirChapterWorkspaceAudit("project/1", "chapter/1");
  await resolveMemoirNarrativeGap("project/1", "gap/1", "not-applicable", {
    idempotency_key: "decision-1",
    note: "该项不适用于本章",
  });

  const apiRequests = requests.filter((request) => request.url.includes("/api/memoir/projects/"));
  assert.equal(apiRequests[0].url, "http://backend.local:8000/api/memoir/projects/project%2F1/chapters/chapter%2F1/workspace");
  assert.equal(apiRequests[1].url, "http://backend.local:8000/api/memoir/projects/project%2F1/chapters/chapter%2F1/contract/revisions");
  assert.equal(apiRequests[1].data.copy_from_contract_id, "contract/1");
  assert.equal(apiRequests[2].url, "http://backend.local:8000/api/memoir/projects/project%2F1/chapter-contracts/contract%2F1/stories/story%2F1/include");
  assert.deepEqual(apiRequests[2].data, {
    idempotency_key: "include-1",
    reason: "属于本章",
  });
  assert.equal(apiRequests[3].url, "http://backend.local:8000/api/memoir/projects/project%2F1/chapters/chapter%2F1/workspace/audits");
  assert.deepEqual(apiRequests[3].data, {
    include_semantic_audit: true,
    ai_model: "deepseek-v4-flash",
  });
  assert.equal(apiRequests[4].url, "http://backend.local:8000/api/memoir/projects/project%2F1/gaps/gap%2F1/not-applicable");
  assert.deepEqual(apiRequests[4].data, {
    idempotency_key: "decision-1",
    note: "该项不适用于本章",
  });
  delete global.wx;
});
