const assert = require("node:assert/strict");
const test = require("node:test");

const { handleMemoirApiRequest } = require("../src/router");
const { COLLECTIONS } = require("../src/collections");
const { MemoryStore } = require("./memoryStore");

function request(event, options = {}) {
  return handleMemoirApiRequest(
    event,
    { openid: options.openid || "openid_a" },
    { store: options.store || new MemoryStore() },
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

test("returns 400 when path is missing", async () => {
  const response = await handleMemoirApiRequest({ method: "GET" });

  assert.equal(response.statusCode, 400);
  assert.equal(response.data.detail, "缺少请求路径");
});

test("returns 404 for routes that are not implemented yet", async () => {
  const response = await handleMemoirApiRequest({
    method: "GET",
    path: "/api/mobile/memoir/unknown",
  });

  assert.equal(response.statusCode, 404);
  assert.equal(response.data.detail, "接口未实现");
});

test("creates the current user's default memoir project", async () => {
  const store = new MemoryStore();
  const response = await request({
    method: "GET",
    path: "/api/mobile/memoir/project",
  }, { store });

  assert.equal(response.statusCode, 200);
  assert.equal(response.data.type, "memoir");
  assert.equal(response.data.title, "我的回忆录");

  const otherUserResponse = await request({
    method: "GET",
    path: "/api/mobile/memoir/project",
  }, { store, openid: "openid_b" });

  assert.notEqual(otherUserResponse.data.id, response.data.id);
});

test("seeds a current session with template questions", async () => {
  const store = new MemoryStore();
  const response = await request({
    method: "GET",
    path: "/api/mobile/memoir/sessions/current",
  }, { store });

  assert.equal(response.statusCode, 200);
  assert.equal(response.data.session.status, "active");
  assert.equal(response.data.questions.length, 5);
  assert.equal(response.data.pending_question_count, 5);
  assert.equal(response.data.current_question.prompt, "你小时候最常去的一个地方是哪里？那里有什么声音、气味，或者你现在还记得的细节？");
});

test("answers a question and advances to the next pending question", async () => {
  const store = new MemoryStore();
  const state = await request({
    method: "GET",
    path: "/api/mobile/memoir/sessions/current",
  }, { store });
  const firstQuestionId = state.data.current_question.id;

  const answer = await request({
    method: "POST",
    path: "/api/mobile/memoir/answers",
    data: {
      question_id: firstQuestionId,
      text: "我小时候经常去外婆家。",
    },
  }, { store });

  assert.equal(answer.statusCode, 200);
  assert.equal(answer.data.transcript_raw, "我小时候经常去外婆家。");
  assert.equal(answer.data.status, "answered");

  const nextQuestion = await request({
    method: "GET",
    path: "/api/mobile/memoir/questions/next",
  }, { store });

  assert.equal(nextQuestion.statusCode, 200);
  assert.notEqual(nextQuestion.data.id, firstQuestionId);

  const questions = await store.findMany(COLLECTIONS.questions, { openid: "openid_a" });
  assert.equal(questions.find((question) => question.id === firstQuestionId).status, "answered");
});

test("skips a pending question", async () => {
  const store = new MemoryStore();
  const state = await request({
    method: "GET",
    path: "/api/mobile/memoir/sessions/current",
  }, { store });
  const firstQuestionId = state.data.current_question.id;

  const response = await request({
    method: "POST",
    path: `/api/mobile/memoir/questions/${firstQuestionId}/skip`,
  }, { store });

  assert.equal(response.statusCode, 200);
  assert.equal(response.data.completed_question_count, 1);
  assert.notEqual(response.data.current_question.id, firstQuestionId);
});
