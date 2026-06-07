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
  assert.equal(response.data.id, "openid.with/slash");
  assert.equal(response.data.storage_owner_id, "openid_with_slash");
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

  assert.equal(answer.statusCode, 201);
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
      file_id: "cloud://env/memoir/audio/openid_a/answer.mp3",
      cloud_path: "memoir/audio/openid_a/answer.mp3",
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

  assert.equal(answer.statusCode, 201);
  assert.equal(answer.data.audio_file_id, audioFile.data.id);
  assert.equal(answer.data.duration_seconds, 7);

  const answers = await store.findMany(COLLECTIONS.answers, { openid: "openid_a" });
  assert.equal(answers[0].audio_file_id, audioFile.data.id);
  assert.equal(answers[0].duration_seconds, 7);
});

test("rejects malformed answer request bodies", async () => {
  const response = await request({
    method: "POST",
    path: "/api/mobile/memoir/answers",
    data: {
      text: "缺少问题 ID",
    },
  });

  assert.equal(response.statusCode, 400);
  assert.equal(response.data.detail, "缺少问题 ID");
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
      file_id: "cloud://env/memoir/audio/openid_a/sample.mp3",
      cloud_path: "memoir/audio/openid_a/sample.mp3",
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
  assert.equal(files[0].file_id, "cloud://env/memoir/audio/openid_a/sample.mp3");
});

test("rejects cloud audio paths outside the current user directory", async () => {
  const response = await request({
    method: "POST",
    path: "/api/mobile/memoir/audio-files",
    data: {
      file_id: "cloud://env/memoir/audio/openid_b/sample.mp3",
      cloud_path: "memoir/audio/openid_b/sample.mp3",
      mime_type: "audio/mpeg",
      size_bytes: 1200,
    },
  });

  assert.equal(response.statusCode, 403);
  assert.equal(response.data.detail, "音频路径不属于当前用户");
});

test("rejects unsupported cloud audio formats", async () => {
  const response = await request({
    method: "POST",
    path: "/api/mobile/memoir/audio-files",
    data: {
      file_id: "cloud://env/memoir/audio/openid_a/sample.aac",
      cloud_path: "memoir/audio/openid_a/sample.aac",
      mime_type: "audio/aac",
      size_bytes: 1200,
    },
  });

  assert.equal(response.statusCode, 400);
  assert.equal(response.data.detail, "仅支持 mp3、wav、ogg、pcm 音频文件");
});

test("rejects cloud audio mime types that do not match the extension", async () => {
  const response = await request({
    method: "POST",
    path: "/api/mobile/memoir/audio-files",
    data: {
      file_id: "cloud://env/memoir/audio/openid_a/sample.mp3",
      cloud_path: "memoir/audio/openid_a/sample.mp3",
      mime_type: "audio/wav",
      size_bytes: 1200,
    },
  });

  assert.equal(response.statusCode, 400);
  assert.equal(response.data.detail, "音频类型与文件扩展名不匹配");
});

test("rejects oversized cloud audio files", async () => {
  const response = await request({
    method: "POST",
    path: "/api/mobile/memoir/audio-files",
    data: {
      file_id: "cloud://env/memoir/audio/openid_a/sample.mp3",
      cloud_path: "memoir/audio/openid_a/sample.mp3",
      mime_type: "audio/mpeg",
      size_bytes: 20 * 1024 * 1024 + 1,
    },
  });

  assert.equal(response.statusCode, 413);
  assert.equal(response.data.detail, "音频文件不能超过 20MB");
});

test("rejects cloud file ids that do not match the registered path", async () => {
  const response = await request({
    method: "POST",
    path: "/api/mobile/memoir/audio-files",
    data: {
      file_id: "cloud://env/memoir/audio/openid_a/other.mp3",
      cloud_path: "memoir/audio/openid_a/sample.mp3",
      mime_type: "audio/mpeg",
      size_bytes: 1200,
    },
  });

  assert.equal(response.statusCode, 403);
  assert.equal(response.data.detail, "云存储文件 ID 与音频路径不匹配");
});

test("transcribes a registered cloud audio file", async () => {
  const store = new MemoryStore();
  const audioFile = await request({
    method: "POST",
    path: "/api/mobile/memoir/audio-files",
    data: {
      file_id: "cloud://env/memoir/audio/openid_a/sample.mp3",
      cloud_path: "memoir/audio/openid_a/sample.mp3",
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
        assert.equal(fileID, "cloud://env/memoir/audio/openid_a/sample.mp3");
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

test("does not transcribe another user's registered cloud audio file", async () => {
  const store = new MemoryStore();
  const audioFile = await request({
    method: "POST",
    path: "/api/mobile/memoir/audio-files",
    data: {
      file_id: "cloud://env/memoir/audio/openid_a/sample.mp3",
      cloud_path: "memoir/audio/openid_a/sample.mp3",
      mime_type: "audio/mpeg",
      size_bytes: 1200,
    },
  }, { store, openid: "openid_a" });

  const response = await request({
    method: "POST",
    path: `/api/mobile/memoir/audio-files/${audioFile.data.id}/transcribe`,
  }, {
    store,
    openid: "openid_b",
    storage: {
      async downloadFile() {
        throw new Error("should not download another user's file");
      },
    },
    asr: {
      async transcribe() {
        throw new Error("should not transcribe another user's file");
      },
    },
  });

  assert.equal(response.statusCode, 404);
  assert.equal(response.data.detail, "音频文件不存在");
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

test("returns memoir story cards and summary", async () => {
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
      text: "我记得外婆家的院子。",
    },
  }, { store });
  const draft = await request({
    method: "POST",
    path: `/api/mobile/memoir/answers/${answer.data.id}/draft`,
  }, {
    store,
    drafting: {
      async generate() {
        return { text: "外婆家的院子，是我童年里最清晰的地方。" };
      },
    },
  });

  const stories = await request({
    method: "GET",
    path: "/api/mobile/memoir/stories",
  }, { store });

  assert.equal(stories.statusCode, 200);
  assert.equal(stories.data.total, 1);
  assert.equal(stories.data.stories[0].id, answer.data.id);
  assert.equal(stories.data.stories[0].answer_id, answer.data.id);
  assert.equal(stories.data.stories[0].question_id, state.data.current_question.id);
  assert.equal(stories.data.stories[0].question_prompt, state.data.current_question.prompt);
  assert.equal(stories.data.stories[0].answer_preview, "我记得外婆家的院子。");
  assert.equal(stories.data.stories[0].answer_status, "drafted");
  assert.equal(stories.data.stories[0].draft_id, draft.data.id);
  assert.equal(stories.data.stories[0].draft_status, "draft");
  assert.equal(stories.data.stories[0].draft_preview, "外婆家的院子，是我童年里最清晰的地方。");

  const detail = await request({
    method: "GET",
    path: `/api/mobile/memoir/stories/${answer.data.id}`,
  }, { store });

  assert.equal(detail.statusCode, 200);
  assert.equal(detail.data.story.answer_id, answer.data.id);
  assert.equal(detail.data.question.id, state.data.current_question.id);
  assert.equal(detail.data.answer.id, answer.data.id);
  assert.equal(detail.data.answer.transcript_edited, "我记得外婆家的院子。");
  assert.equal(detail.data.draft.id, draft.data.id);
  assert.equal(detail.data.draft.content_edited, "外婆家的院子，是我童年里最清晰的地方。");

  const summary = await request({
    method: "GET",
    path: "/api/mobile/memoir/summary",
  }, { store });

  assert.equal(summary.statusCode, 200);
  assert.deepEqual(summary.data, {
    total_story_count: 1,
    weekly_story_count: 1,
    pending_review_count: 1,
    confirmed_story_count: 0,
    total_question_count: 5,
    completed_question_count: 1,
    progress_percent: 20,
  });
});

test("processes session memory surfaces coverage and question candidates", async () => {
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
      text: "我第一次去重庆厂里上班，是老张带我去的。",
    },
  }, { store });
  const payload = {
    people: [{
      canonical_name: "老张",
      relationship: "同事",
      description: "带受访者去重庆厂里的人",
      confidence: 0.85,
      evidence_quote: "老张带我去的",
      answer_id: answer.data.id,
    }],
    events: [{
      title: "第一次去重庆工作",
      life_stage: "青年",
      time_text: "第一次去重庆厂里上班",
      process: "老张带受访者去重庆厂里上班",
      emotion: "骄傲",
      importance: 0.9,
      confidence: 0.9,
      evidence_quote: "第一次去重庆厂里上班",
      answer_id: answer.data.id,
    }],
    places: [{
      name: "重庆厂",
      place_type: "工作地点",
      evidence_quote: "重庆厂里上班",
      answer_id: answer.data.id,
    }],
    themes: [{
      name: "离乡",
      description: "离开家去外地工作",
      strength: 0.8,
      mention_count: 1,
      evidence_quote: "去重庆厂里上班",
      answer_id: answer.data.id,
    }],
    open_questions: [{
      question: "第一次去重庆厂那天，是谁送你出门的？",
      question_type: "scene_detail",
      reason: "关键事件缺少出发场景",
      priority: 0.82,
      target_memory_type: "event",
      target_title: "第一次去重庆工作",
      evidence_quote: "老张带我去的",
      answer_id: answer.data.id,
    }],
    session_summary: "本次访谈沉淀了第一次去重庆工作的经历。",
    story_fragment: "第一次去重庆厂里上班，是老张带我去的。",
  };
  const extraction = {
    async extract(input) {
      assert.equal(input.session.id, state.data.session.id);
      assert.equal(input.turns[0].answer_id, answer.data.id);
      return {
        payload,
        model: "fake-memory",
        prompt: "prompt",
      };
    },
  };

  const first = await request({
    method: "POST",
    path: `/api/mobile/memoir/sessions/${state.data.session.id}/process`,
  }, { store, memoryExtraction: extraction });
  const second = await request({
    method: "POST",
    path: `/api/mobile/memoir/sessions/${state.data.session.id}/process`,
  }, { store, memoryExtraction: extraction });

  assert.equal(first.statusCode, 200);
  assert.equal(first.data.people[0].canonical_name, "老张");
  assert.equal(first.data.events[0].title, "第一次去重庆工作");
  assert.equal(first.data.open_questions[0].question_type, "scene");
  assert.equal(second.data.people[0].id, first.data.people[0].id);

  const summary = await request({
    method: "GET",
    path: `/api/mobile/memoir/sessions/${state.data.session.id}/summary`,
  }, { store });
  assert.equal(summary.data.summary, "本次访谈沉淀了第一次去重庆工作的经历。");

  const people = await request({ method: "GET", path: "/api/mobile/memoir/memories/people" }, { store });
  const events = await request({ method: "GET", path: "/api/mobile/memoir/memories/events" }, { store });
  const places = await request({ method: "GET", path: "/api/mobile/memoir/memories/places" }, { store });
  const themes = await request({ method: "GET", path: "/api/mobile/memoir/memories/themes" }, { store });
  assert.equal(people.data.length, 1);
  assert.equal(events.data.length, 1);
  assert.equal(places.data.length, 1);
  assert.equal(themes.data.length, 1);

  const coverage = await request({
    method: "POST",
    path: "/api/mobile/memoir/coverage/recalculate",
  }, { store });
  assert.equal(coverage.data.length, 10);
  assert.ok(coverage.data.some((item) => item.key === "first_job" && item.coverage_score > 0));

  const candidates = await request({
    method: "POST",
    path: "/api/mobile/memoir/question-candidates/generate",
  }, { store });
  assert.ok(candidates.data.some((item) => item.question.startsWith("第一次去重庆厂")));

  const selected = await request({
    method: "POST",
    path: `/api/mobile/memoir/question-candidates/${candidates.data[0].id}/select`,
  }, { store });
  assert.equal(selected.statusCode, 200);
  assert.equal(selected.data.source, "ai_generated");

  const currentAfterSelect = await request({
    method: "GET",
    path: "/api/mobile/memoir/sessions/current",
  }, { store });
  assert.equal(currentAfterSelect.data.current_question.id, selected.data.id);
  assert.equal(currentAfterSelect.data.session.current_question_id, selected.data.id);

  const nextAfterSelect = await request({
    method: "GET",
    path: "/api/mobile/memoir/questions/next",
  }, { store });
  assert.equal(nextAfterSelect.data.id, selected.data.id);

  const outputs = await request({ method: "GET", path: "/api/mobile/memoir/outputs" }, { store });
  assert.equal(outputs.data[0].output_type, "story_fragment");

  assert.equal((await store.findMany(COLLECTIONS.personMemories, { openid: "openid_a" })).length, 1);
  assert.equal((await store.findMany(COLLECTIONS.memoryEvidence, { openid: "openid_a" })).length, 5);
});
