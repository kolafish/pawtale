require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

const SESSION_STORE_KEY = "pawtale_memoir_session";
const INPUT_DRAFT_STORE_KEY = "pawtale_memoir_input_drafts";
const ANSWER_SESSION_DRAFT_STORE_KEY = "pawtale_memoir_answer_session_drafts";

function clone(value) {
  return value === undefined ? undefined : JSON.parse(JSON.stringify(value));
}

function installWxStorageMock(initialStorage = {}) {
  const storage = clone(initialStorage) || {};

  global.wx = {
    getStorageSync(key) {
      return clone(storage[key]);
    },
    setStorageSync(key, value) {
      storage[key] = clone(value);
    },
    removeStorageSync(key) {
      delete storage[key];
    },
  };

  return { storage };
}

function answerSessionDraftsModule() {
  delete require.cache[require.resolve("../stores/memoir-answer-session-drafts.ts")];
  return require("../stores/memoir-answer-session-drafts.ts");
}

function sessionStoreModule() {
  delete require.cache[require.resolve("../stores/session.ts")];
  delete require.cache[require.resolve("../stores/memoir-answer-session-drafts.ts")];
  delete require.cache[require.resolve("../stores/memoir-input-drafts.ts")];
  return require("../stores/session.ts");
}

test("writes a remote answer session draft snapshot", () => {
  installWxStorageMock();
  const { readMemoirAnswerSessionDraft, writeMemoirAnswerSessionDraftFromRemote } = answerSessionDraftsModule();

  const draft = writeMemoirAnswerSessionDraftFromRemote({
    id: "answer_session_1",
    project_id: "project_1",
    session_id: "session_1",
    question_id: "question_1",
    answer_id: null,
    status: "draft",
    segment_count: 2,
    duration_seconds: 130,
    transcript_raw: "旧聚合",
    transcript_edited: "旧聚合",
    submitted_at: null,
    created_at: "2026-06-01T00:00:00.000Z",
    updated_at: "2026-06-01T00:01:00.000Z",
    segments: [
      {
        id: "segment_2",
        project_id: "project_1",
        session_id: "session_1",
        question_id: "question_1",
        answer_session_id: "answer_session_1",
        sort_order: 1,
        status: "transcribed",
        duration_seconds: 80,
        transcript_raw: "第二段",
        transcript_edited: "第二段修订",
        created_at: "2026-06-01T00:01:00.000Z",
        updated_at: "2026-06-01T00:01:00.000Z",
      },
      {
        id: "segment_1",
        project_id: "project_1",
        session_id: "session_1",
        question_id: "question_1",
        answer_session_id: "answer_session_1",
        sort_order: 0,
        status: "transcribed",
        duration_seconds: 50,
        transcript_raw: "第一段",
        transcript_edited: "",
        created_at: "2026-06-01T00:00:00.000Z",
        updated_at: "2026-06-01T00:00:00.000Z",
      },
    ],
  });

  assert.equal(draft.answer_session_id, "answer_session_1");
  assert.equal(draft.pending_upload_count, 0);
  assert.equal(draft.merged_text, "第一段\n\n第二段修订");
  assert.deepEqual(draft.segments.map((segment) => segment.answer_segment_id), ["segment_1", "segment_2"]);

  const cached = readMemoirAnswerSessionDraft("project_1", "question_1");
  assert.equal(cached.answer_session_id, "answer_session_1");
  assert.equal(cached.merged_text, "第一段\n\n第二段修订");
});

test("tracks local pending answer session draft segments", () => {
  installWxStorageMock();
  const {
    clearMemoirAnswerSessionDraft,
    readMemoirAnswerSessionDraft,
    writeMemoirAnswerSessionDraft,
  } = answerSessionDraftsModule();

  const draft = writeMemoirAnswerSessionDraft({
    project_id: "project_1",
    session_id: "session_1",
    question_id: "question_1",
    status: "local",
    created_at: "2026-06-01T00:00:00.000Z",
    segments: [
      {
        local_id: "local_2",
        temp_file_path: "/tmp/answer-2.mp3",
        sort_order: 1,
        status: "local",
        duration_seconds: 60,
        transcript_raw: "",
        transcript_edited: "",
        created_at: "2026-06-01T00:01:00.000Z",
        updated_at: "2026-06-01T00:01:00.000Z",
      },
      {
        local_id: "local_1",
        answer_segment_id: "segment_1",
        audio_file_id: "audio_1",
        sort_order: 0,
        status: "transcribed",
        duration_seconds: 80,
        transcript_raw: "已经转写",
        transcript_edited: "已经修订",
        created_at: "2026-06-01T00:00:00.000Z",
        updated_at: "2026-06-01T00:00:00.000Z",
      },
    ],
  });

  assert.equal(draft.pending_upload_count, 1);
  assert.equal(draft.merged_text, "已经修订");
  assert.deepEqual(draft.segments.map((segment) => segment.local_id), ["local_1", "local_2"]);

  clearMemoirAnswerSessionDraft("project_1", "question_1");
  assert.equal(readMemoirAnswerSessionDraft("project_1", "question_1"), undefined);
});

test("identity reset clears answer session drafts", () => {
  const mock = installWxStorageMock({
    [SESSION_STORE_KEY]: {
      cache_identity: { user_id: "user_1", project_id: "project_1" },
      project: { id: "project_1" },
    },
    [INPUT_DRAFT_STORE_KEY]: {
      version: 1,
      questions: {
        question_1: {
          question_id: "question_1",
          answer_text: "旧草稿",
          updated_at: "2026-06-01T00:00:00.000Z",
        },
      },
      qa_details: {},
      editors: {},
    },
    [ANSWER_SESSION_DRAFT_STORE_KEY]: {
      version: 1,
      drafts: {
        "project_1:question_1": {
          project_id: "project_1",
          session_id: "session_1",
          question_id: "question_1",
          status: "local",
          segments: [],
          merged_text: "",
          pending_upload_count: 0,
          created_at: "2026-06-01T00:00:00.000Z",
          updated_at: "2026-06-01T00:00:00.000Z",
        },
      },
    },
  });
  const { ensureMemoirStoreIdentity } = sessionStoreModule();

  ensureMemoirStoreIdentity({ user_id: "user_2", project_id: "project_2" });

  assert.equal(mock.storage[INPUT_DRAFT_STORE_KEY], undefined);
  assert.equal(mock.storage[ANSWER_SESSION_DRAFT_STORE_KEY], undefined);
  assert.deepEqual(mock.storage[SESSION_STORE_KEY].cache_identity, {
    user_id: "user_2",
    project_id: "project_2",
  });
});
