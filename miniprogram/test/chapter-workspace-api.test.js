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
    createMemoirChapterContractRevision,
    decideMemoirClaim,
    decideMemoirStory,
    enqueueMemoirChapterWorkspaceAudit,
    excludeMemoirStoryFromChapter,
    getMemoirChapterWorkspace,
    mergeMemoirStory,
    mergeMemoirChapterStructures,
    moveMemoirStoryBetweenChapterNodes,
    reorderMemoirChapterNodes,
    resolveMemoirNarrativeGap,
    resolveMemoirStorySlot,
    splitMemoirChapterStructure,
    updateMemoirChapterNode,
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
  await excludeMemoirStoryFromChapter(
    "project/1",
    "contract/1",
    "story/1",
    {
      idempotency_key: "chapter-exclusion-1",
      reason: "移到下一章",
    },
  );
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
  await updateMemoirChapterNode("project/1", "contract/1", "node/1", {
    title: "新的节点标题",
    purpose: "新的叙事作用",
    required: true,
  });
  await reorderMemoirChapterNodes("project/1", "contract/1", {
    node_ids: ["node/2", "node/1"],
  });
  await moveMemoirStoryBetweenChapterNodes(
    "project/1",
    "contract/1",
    "story/1",
    { source_node_id: "node/1", target_node_id: "node/2" },
  );
  await mergeMemoirStory("project/1", "story/1", {
    retained_story_id: "story/2",
    idempotency_key: "merge-1",
    reason: "确认是同一个故事",
  });
  await createMemoirChapterContractRevision("project/1", "chapter/1", {
    idempotency_key: "contract-revision-2",
    objective: "讲清离家的原因和影响",
    reader_takeaway: "理解人生选择",
    time_scope_text: "青年时期",
    time_start: null,
    time_end: null,
    core_questions: [{ key: "reason", question: "为什么离家？" }],
    constraints: {
      include_story_ids: ["story/1"],
      exclude_story_ids: [],
      include_topics: [],
      exclude_topics: [],
    },
    copy_from_contract_id: "contract/1",
    copy_nodes: true,
  });
  await splitMemoirChapterStructure("project/1", {
    idempotency_key: "split-1",
    source_contract_id: "contract/1",
    node_ids: ["node/2"],
    target_title: "第一次离家",
    target_objective: "完整讲述离家经过",
    target_reader_takeaway: "理解离家的改变",
    target_time_scope_text: "十八岁",
    target_core_questions: [{ key: "departure", question: "离家发生了什么？" }],
  });
  await mergeMemoirChapterStructures("project/1", {
    idempotency_key: "merge-chapter-1",
    source_contract_id: "contract/1",
    target_chapter_id: "chapter/2",
  });

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
  assert.equal(apiRequests[5].url, "http://backend.local:8000/api/memoir/projects/project%2F1/chapter-contracts/contract%2F1/stories/story%2F1/exclude");
  assert.deepEqual(apiRequests[5].data, {
    idempotency_key: "chapter-exclusion-1",
    reason: "移到下一章",
  });
  assert.equal(apiRequests[6].url, "http://backend.local:8000/api/memoir/projects/project%2F1/stories/story%2F1/slots/time%2Fplace/resolve");
  assert.deepEqual(apiRequests[6].data, {
    idempotency_key: "slot-decision-1",
    note: "叙述者暂时无法补充",
    status: "accepted_incomplete",
  });
  assert.equal(apiRequests[7].url, "http://backend.local:8000/api/memoir/projects/project%2F1/chapter-contracts/contract%2F1/confirm");
  assert.deepEqual(apiRequests[7].data, {});
  assert.equal(apiRequests[8].url, "http://backend.local:8000/api/memoir/projects/project%2F1/chapter-contracts/contract%2F1/nodes/node%2F1");
  assert.equal(apiRequests[8].method, "PATCH");
  assert.deepEqual(apiRequests[8].data, {
    title: "新的节点标题",
    purpose: "新的叙事作用",
    required: true,
  });
  assert.equal(apiRequests[9].url, "http://backend.local:8000/api/memoir/projects/project%2F1/chapter-contracts/contract%2F1/nodes/reorder");
  assert.deepEqual(apiRequests[9].data, { node_ids: ["node/2", "node/1"] });
  assert.equal(apiRequests[10].url, "http://backend.local:8000/api/memoir/projects/project%2F1/chapter-contracts/contract%2F1/stories/story%2F1/move");
  assert.deepEqual(apiRequests[10].data, {
    source_node_id: "node/1",
    target_node_id: "node/2",
  });
  assert.equal(apiRequests[11].url, "http://backend.local:8000/api/memoir/projects/project%2F1/stories/story%2F1/merge");
  assert.deepEqual(apiRequests[11].data, {
    retained_story_id: "story/2",
    idempotency_key: "merge-1",
    reason: "确认是同一个故事",
  });
  assert.equal(apiRequests[12].url, "http://backend.local:8000/api/memoir/projects/project%2F1/chapters/chapter%2F1/contract/revisions");
  assert.equal(apiRequests[12].method, "POST");
  assert.deepEqual(apiRequests[12].data.core_questions, [
    { key: "reason", question: "为什么离家？" },
  ]);
  assert.equal(apiRequests[13].url, "http://backend.local:8000/api/memoir/projects/project%2F1/chapter-structures/split");
  assert.deepEqual(apiRequests[13].data.node_ids, ["node/2"]);
  assert.equal(apiRequests[13].data.target_title, "第一次离家");
  assert.equal(apiRequests[14].url, "http://backend.local:8000/api/memoir/projects/project%2F1/chapter-structures/merge");
  assert.deepEqual(apiRequests[14].data, {
    idempotency_key: "merge-chapter-1",
    source_contract_id: "contract/1",
    target_chapter_id: "chapter/2",
  });
  delete global.wx;
});
