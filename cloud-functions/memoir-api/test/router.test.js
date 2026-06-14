const assert = require("node:assert/strict");
const test = require("node:test");

const { handleMemoirApiRequest } = require("../src/router");
const { MemoryStore } = require("./memoryStore");

function request(event, options = {}) {
  return handleMemoirApiRequest(
    event,
    { openid: options.openid || "openid_a" },
    {
      memoryExtraction: options.memoryExtraction,
      storage: options.storage,
      store: options.store || new MemoryStore(),
    },
  );
}

test("returns the memoir cloud function health response", async () => {
  const response = await handleMemoirApiRequest({
    method: "GET",
    path: "/api/mobile/memoir/health",
  });

  assert.equal(response.statusCode, 200);
  assert.deepEqual(response.data, {
    ok: true,
    service: "memoir-api",
  });
});

test("normalizes method and path", async () => {
  const response = await handleMemoirApiRequest({
    method: "get",
    path: "api/mobile/memoir/health",
  });

  assert.equal(response.statusCode, 200);
});

test("returns the current cloud user identity", async () => {
  const response = await request({
    method: "GET",
    path: "/api/mobile/memoir/me",
  }, { openid: "openid.with/slash" });

  assert.equal(response.statusCode, 200);
  assert.equal(response.data.openid, "openid.with/slash");
  assert.equal(response.data.display_name, "微信用户");
  assert.equal(response.data.storage_owner_id, "openid_with_slash");
  assert.equal(response.data.active_project.title, "微信用户的回忆录");
  assert.equal(response.data.projects.length, 1);
});

test("returns 400 when path is missing", async () => {
  const response = await handleMemoirApiRequest({ method: "GET" });

  assert.equal(response.statusCode, 400);
  assert.equal(response.data.detail, "缺少请求路径");
});

test("returns 404 for unknown routes", async () => {
  const response = await handleMemoirApiRequest({
    method: "GET",
    path: "/api/mobile/memoir/unknown",
  });

  assert.equal(response.statusCode, 404);
  assert.equal(response.data.detail, "接口未实现");
});

test("creates and isolates default memoir projects by openid", async () => {
  const store = new MemoryStore();
  const first = await request({
    method: "GET",
    path: "/api/mobile/memoir/project",
  }, { store, openid: "openid_a" });
  const second = await request({
    method: "GET",
    path: "/api/mobile/memoir/project",
  }, { store, openid: "openid_b" });

  assert.equal(first.statusCode, 200);
  assert.equal(first.data.type, "memoir");
  assert.equal(first.data.title, "微信用户的回忆录");
  assert.notEqual(second.data.id, first.data.id);
});

test("renames the generic default project when WeChat profile arrives", async () => {
  const store = new MemoryStore();
  const initialProject = await request({
    method: "GET",
    path: "/api/mobile/memoir/project",
  }, { store });

  await request({
    method: "PATCH",
    path: "/api/mobile/memoir/me",
    data: {
      wechat_nickname: "李阿姨",
    },
  }, { store });

  const updatedProject = await request({
    method: "GET",
    path: "/api/mobile/memoir/project",
  }, { store });
  assert.equal(updatedProject.data.id, initialProject.data.id);
  assert.equal(updatedProject.data.title, "李阿姨的回忆录");
});

test("creates renames and activates multiple memoir projects", async () => {
  const store = new MemoryStore();
  const first = await request({
    method: "GET",
    path: "/api/mobile/memoir/project",
  }, { store });
  const second = await request({
    method: "POST",
    path: "/api/mobile/memoir/projects",
    data: {
      title: "父亲的故事",
      elder_name: "父亲",
    },
  }, { store });
  const renamed = await request({
    method: "PATCH",
    path: `/api/mobile/memoir/projects/${second.data.id}`,
    data: {
      title: "父亲的回忆录",
    },
  }, { store });
  await request({
    method: "POST",
    path: `/api/mobile/memoir/projects/${first.data.id}/activate`,
  }, { store });
  const projects = await request({
    method: "GET",
    path: "/api/mobile/memoir/projects",
  }, { store });

  assert.equal(second.statusCode, 201);
  assert.equal(renamed.data.title, "父亲的回忆录");
  assert.equal(projects.data.projects.length, 2);
  assert.equal(projects.data.active_project_id, first.data.id);
});

test("initializes current session and dashboard without legacy answer-session state", async () => {
  const store = new MemoryStore();
  const session = await request({
    method: "GET",
    path: "/api/mobile/memoir/sessions/current",
  }, { store });
  const dashboard = await request({
    method: "GET",
    path: "/api/mobile/memoir/dashboard",
  }, { store });

  assert.equal(session.statusCode, 200);
  assert.equal(session.data.current_question.id, session.data.session.current_question_id);
  assert.ok(session.data.questions.some((question) => question.id === session.data.current_question.id));
  assert.equal(dashboard.statusCode, 200);
  assert.equal(Object.hasOwn(dashboard.data, "active_answer_session"), false);
  assert.equal(dashboard.data.interview.session.id, session.data.session.id);
});

test("legacy memoir write routes are no longer exposed", async () => {
  const store = new MemoryStore();
  const session = await request({
    method: "GET",
    path: "/api/mobile/memoir/sessions/current",
  }, { store });
  const questionId = session.data.current_question.id;
  const routes = [
    { method: "POST", path: "/api/mobile/memoir/answers", data: { question_id: questionId, text: "旧回答" } },
    { method: "PATCH", path: "/api/mobile/memoir/answers/legacy-answer", data: { text: "旧编辑" } },
    { method: "DELETE", path: "/api/mobile/memoir/answers/legacy-answer" },
    { method: "POST", path: "/api/mobile/memoir/answers/legacy-answer/draft", data: {} },
    { method: "PATCH", path: "/api/mobile/memoir/drafts/legacy-draft", data: { content_edited: "旧草稿" } },
    { method: "POST", path: "/api/mobile/memoir/drafts/legacy-draft/accept" },
    { method: "POST", path: "/api/mobile/memoir/audio-files", data: {} },
    { method: "POST", path: `/api/mobile/memoir/questions/${questionId}/skip` },
    { method: "POST", path: `/api/mobile/memoir/questions/${questionId}/answer-session` },
    { method: "GET", path: "/api/mobile/memoir/answer-sessions/legacy-session" },
    { method: "POST", path: "/api/mobile/memoir/answer-sessions/legacy-session/segments", data: {} },
    { method: "POST", path: "/api/mobile/memoir/answer-sessions/legacy-session/submit" },
    { method: "PATCH", path: "/api/mobile/memoir/answer-segments/legacy-segment", data: {} },
    { method: "POST", path: "/api/mobile/memoir/tts-assets", data: {} },
    { method: "GET", path: "/api/mobile/memoir/tts-assets/legacy-asset" },
    { method: "GET", path: "/api/mobile/memoir/tts-jobs/legacy-job" },
    { method: "POST", path: "/api/mobile/memoir/tts-jobs/legacy-job/process" },
    { method: "POST", path: "/api/mobile/memoir/question-candidates/legacy-candidate/select" },
  ];

  for (const route of routes) {
    const response = await request(route, { store });
    assert.equal(response.statusCode, 404, `${route.method} ${route.path}`);
  }
});
