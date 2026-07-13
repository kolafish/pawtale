require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

function resetModules() {
  for (const modulePath of [
    "../services/auth.ts",
    "../services/request.ts",
    "../services/ai-model-settings.ts",
    "../services/memoir-api-client.ts",
    "../services/memoir-followup-task-api.ts",
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

test("follow-up task API uses project-scoped routes and explicit decision payloads", async () => {
  const requests = [];
  installWx(requests);
  resetModules();
  const {
    cannotRecallMemoirFollowupTask,
    deferMemoirFollowupTask,
    excludeMemoirFollowupTask,
    getMemoirFollowupTask,
    listMemoirFollowupTasks,
    switchMemoirFollowupTask,
  } = require("../services/memoir-followup-task-api.ts");

  await listMemoirFollowupTasks("project/1", { chapterId: "chapter/1", limit: 100 });
  await getMemoirFollowupTask("project/1", "task/1");
  await deferMemoirFollowupTask("project/1", "task/1", {
    idempotency_key: "defer-1",
    note: "以后再补",
  });
  await cannotRecallMemoirFollowupTask("project/1", "task/1", {
    idempotency_key: "recall-1",
    note: "询问家人",
    resolution: "ask_family_later",
  });
  await excludeMemoirFollowupTask("project/1", "task/1", {
    idempotency_key: "exclude-1",
    note: "不用于成书",
  });
  await switchMemoirFollowupTask("project/1", "voice/1", {
    current_task_id: "task/1",
    idempotency_key: "switch-1",
    note: "换一个问题",
  });

  const apiRequests = requests.filter((request) => request.url.includes("/api/memoir/projects/"));
  assert.equal(apiRequests[0].url, "http://backend.local:8000/api/memoir/projects/project%2F1/followup-tasks?chapter_id=chapter%2F1&limit=100");
  assert.equal(apiRequests[1].url, "http://backend.local:8000/api/memoir/projects/project%2F1/followup-tasks/task%2F1");
  assert.deepEqual(apiRequests[2].data, { idempotency_key: "defer-1", note: "以后再补" });
  assert.deepEqual(apiRequests[3].data, {
    idempotency_key: "recall-1",
    note: "询问家人",
    resolution: "ask_family_later",
  });
  assert.deepEqual(apiRequests[4].data, { idempotency_key: "exclude-1", note: "不用于成书" });
  assert.equal(apiRequests[5].url, "http://backend.local:8000/api/memoir/projects/project%2F1/voice-sessions/voice%2F1/followup-task/switch");
  assert.deepEqual(apiRequests[5].data, {
    current_task_id: "task/1",
    idempotency_key: "switch-1",
    note: "换一个问题",
  });
  delete global.wx;
});
