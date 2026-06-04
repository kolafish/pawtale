const assert = require("node:assert/strict");
const test = require("node:test");

const { handleMemoirApiRequest } = require("../src/router");
const { COLLECTIONS } = require("../src/collections");
const { MemoryStore } = require("./memoryStore");

function request(event, options = {}) {
  return handleMemoirApiRequest(
    event,
    { openid: options.openid || "openid_a" },
    {
      asr: options.asr,
      drafting: options.drafting,
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

test("answers a question with a linked audio file", async () => {
  const store = new MemoryStore();
  const state = await request({
    method: "GET",
    path: "/api/mobile/memoir/sessions/current",
  }, { store });
  const audioFile = await request({
    method: "POST",
    path: "/api/mobile/memoir/audio-files",
    data: {
      file_id: "cloud://answer-audio",
      cloud_path: "memoir/audio/answer.mp3",
      mime_type: "audio/mpeg",
      size_bytes: 128,
      duration_seconds: 7,
    },
  }, { store });

  const answer = await request({
    method: "POST",
    path: "/api/mobile/memoir/answers",
    data: {
      question_id: state.data.current_question.id,
      text: "我小时候经常去外婆家。",
      audio_file_id: audioFile.data.id,
    },
  }, { store });

  assert.equal(answer.statusCode, 200);
  assert.equal(answer.data.audio_file_id, audioFile.data.id);
  assert.equal(answer.data.duration_seconds, 7);

  const answers = await store.findMany(COLLECTIONS.answers, { openid: "openid_a" });
  assert.equal(answers[0].audio_file_id, audioFile.data.id);
  assert.equal(answers[0].duration_seconds, 7);
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

test("registers a cloud audio file for the current user", async () => {
  const store = new MemoryStore();
  const response = await request({
    method: "POST",
    path: "/api/mobile/memoir/audio-files",
    data: {
      file_id: "cloud://file-id",
      cloud_path: "memoir/audio/sample.mp3",
      mime_type: "audio/mpeg",
      size_bytes: 1200,
      duration_seconds: 9,
    },
  }, { store });

  assert.equal(response.statusCode, 201);
  assert.equal(response.data.mime_type, "audio/mpeg");
  assert.equal(response.data.size_bytes, 1200);
  assert.equal(response.data.duration_seconds, 9);

  const files = await store.findMany(COLLECTIONS.audioFiles, { openid: "openid_a" });
  assert.equal(files.length, 1);
  assert.equal(files[0].file_id, "cloud://file-id");
});

test("transcribes a registered cloud audio file", async () => {
  const store = new MemoryStore();
  const audioFile = await request({
    method: "POST",
    path: "/api/mobile/memoir/audio-files",
    data: {
      file_id: "cloud://file-id",
      cloud_path: "memoir/audio/sample.mp3",
      mime_type: "audio/mpeg",
      size_bytes: 1200,
      duration_seconds: 9,
    },
  }, { store });

  const response = await request({
    method: "POST",
    path: `/api/mobile/memoir/audio-files/${audioFile.data.id}/transcribe`,
  }, {
    store,
    storage: {
      async downloadFile(fileID) {
        assert.equal(fileID, "cloud://file-id");
        return Buffer.from("audio-bytes");
      },
    },
    asr: {
      async transcribe(audioBytes, file) {
        assert.equal(audioBytes.toString(), "audio-bytes");
        assert.equal(file.mime_type, "audio/mpeg");
        return {
          text: "我的爸爸呀呀呀",
          usage: { total_tokens: 12 },
        };
      },
    },
  });

  assert.equal(response.statusCode, 200);
  assert.deepEqual(response.data, {
    audio_file_id: audioFile.data.id,
    text: "我的爸爸呀呀呀",
    usage: { total_tokens: 12 },
  });

  const files = await store.findMany(COLLECTIONS.audioFiles, { openid: "openid_a" });
  assert.equal(files[0].transcript_text, "我的爸爸呀呀呀");
  assert.equal(files[0].transcript_usage_json, JSON.stringify({ total_tokens: 12 }));
});

test("generates, edits, and accepts a memoir draft", async () => {
  const store = new MemoryStore();
  const state = await request({
    method: "GET",
    path: "/api/mobile/memoir/sessions/current",
  }, { store });
  const answer = await request({
    method: "POST",
    path: "/api/mobile/memoir/answers",
    data: {
      question_id: state.data.current_question.id,
      text: "嗯，我小时候总去外婆家啊。",
    },
  }, { store });

  const draft = await request({
    method: "POST",
    path: `/api/mobile/memoir/answers/${answer.data.id}/draft`,
  }, {
    store,
    drafting: {
      async generate(input) {
        assert.equal(input.questionPrompt, state.data.current_question.prompt);
        assert.equal(input.answerText, "嗯，我小时候总去外婆家啊。");
        return {
          text: "我小时候总去外婆家。",
          usage: { total_tokens: 18 },
        };
      },
    },
  });

  assert.equal(draft.statusCode, 200);
  assert.equal(draft.data.content_raw, "我小时候总去外婆家。");
  assert.equal(draft.data.status, "draft");

  const edited = await request({
    method: "PATCH",
    path: `/api/mobile/memoir/drafts/${draft.data.id}`,
    data: {
      content_edited: "我小时候常去外婆家。",
    },
  }, { store });

  assert.equal(edited.data.content_edited, "我小时候常去外婆家。");

  const accepted = await request({
    method: "POST",
    path: `/api/mobile/memoir/drafts/${draft.data.id}/accept`,
  }, { store });

  assert.equal(accepted.data.status, "accepted");
  const answers = await store.findMany(COLLECTIONS.answers, { openid: "openid_a" });
  assert.equal(answers[0].status, "confirmed");
});
