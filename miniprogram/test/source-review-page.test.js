require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");
const { loadPageModule, mockModule, pageContext } = require("./page-harness");


function reviewItem() {
  return {
    id: "event-1",
    project_id: "project-1",
    status: "needs_review",
    utterance_id: "utterance-1",
    source_index: 3,
    route_action: "hold_for_review",
    target_chapter_id: "chapter-1",
    target_chapter_title: "界限与邀约",
    reason: "回答用途不明确",
    summary_text: "请确认这段话怎么使用",
    source_text: "这句话是我当时想发给他的消息，但最后没有发出去。",
    followup_answer: {
      task_id: "task-1",
      status: "needs_confirmation",
      confirmation_type: "answer_usage",
      answer_kind: null,
      direct_narrative_text: "",
      clarification_text: "",
      fact_resolution_text: "",
      confirmation_question: "这段话要写进书里，还是只用于解释原文？",
      reason: "无法准确判断",
    },
    created_at: "2026-07-14T12:00:00Z",
    updated_at: "2026-07-14T12:00:00Z",
  };
}


function loadSourceReviewPage(voiceApiExports = {}, followupTaskApiExports = {}) {
  mockModule("../services/memoir-voice-api.ts", {
    listMemoirBookSourceReviewItems: async () => ({ items: [reviewItem()], total: 1 }),
    confirmMemoirBookSourceReviewItem: async () => {
      throw new Error("confirmMemoirBookSourceReviewItem was not mocked");
    },
    createMemoirVoiceSession: async () => {
      throw new Error("createMemoirVoiceSession was not mocked");
    },
    ...voiceApiExports,
  });
  mockModule("../services/memoir-followup-task-api.ts", {
    getMemoirFollowupTask: async () => ({
      id: "task-1",
      status: "pending",
      assigned_session_id: null,
    }),
    ...followupTaskApiExports,
  });
  mockModule("../services/navigation.ts", {
    routeAuthError: () => false,
    switchToBook: () => {},
  });
  mockModule("../services/voice-authorization.ts", {
    readVoiceAuthorizationConsent: () => true,
    syncVoiceAuthorizationConsent: async () => ({ id: "consent-1" }),
    VOICE_AUTHORIZATION_PAGE_URL: "/pages/voice-authorization/index",
  });
  return loadPageModule("../pages/source-review/index.ts");
}


test("source review page confirms the full answer as clarification", async () => {
  const confirmations = [];
  const { page } = loadSourceReviewPage({
    confirmMemoirBookSourceReviewItem: async (projectId, eventId, request) => {
      confirmations.push({ projectId, eventId, request });
      return { item: { ...reviewItem(), status: "completed" }, plan: {} };
    },
  });
  const context = pageContext(page);

  await page.onLoad.call(context, { projectId: "project-1", eventId: "event-1" });
  assert.equal(context.data.questionText, "这段话要写进书里，还是只用于解释原文？");
  assert.equal(context.data.chapterTitle, "界限与邀约");
  assert.equal(context.data.sourceText, reviewItem().source_text);
  assert.equal(context.data.choices[0].title, "作为正文素材");

  page.selectAnswerKind.call(context, {
    currentTarget: { dataset: { kind: "clarification" } },
  });
  await page.submitConfirmation.call(context);

  assert.deepEqual(confirmations, [
    {
      projectId: "project-1",
      eventId: "event-1",
      request: {
        followup_answer_kind: "clarification",
        clarification_text: reviewItem().source_text,
      },
    },
  ]);
  assert.equal(context.data.success, true);
  assert.equal(context.data.saving, false);
});

test("source review continues fact confirmation through the existing AI voice chain", async () => {
  const factItem = reviewItem();
  factItem.followup_answer.confirmation_type = "fact_resolution";
  factItem.followup_answer.confirmation_question = "这两个称呼说的是同一个人吗？";
  const created = [];
  const navigations = [];
  global.wx = {
    navigateTo({ url }) { navigations.push(url); },
  };
  const { page } = loadSourceReviewPage({
    listMemoirBookSourceReviewItems: async () => ({ items: [factItem], total: 1 }),
    createMemoirVoiceSession: async (input, options) => {
      created.push({ input, options });
      return { session: { id: "voice-fact-1" } };
    },
  });
  const context = pageContext(page);

  await page.onLoad.call(context, { projectId: "project-1", eventId: "event-1" });
  assert.equal(context.data.confirmationType, "fact_resolution");
  assert.equal(context.data.questionText, "这两个称呼说的是同一个人吗？");
  await page.startFactConfirmation.call(context);

  assert.equal(created[0].input.interview_type, "ai_interview");
  assert.equal(created[0].input.session_goal, "book_completion");
  assert.equal(created[0].input.current_followup_task_id, "task-1");
  assert.equal(created[0].options.projectId, "project-1");
  assert.match(navigations[0], /voiceSessionId=voice-fact-1/);
  assert.match(navigations[0], /projectTitle=%E7%95%8C%E9%99%90%E4%B8%8E%E9%82%80%E7%BA%A6/);
  delete global.wx;
});

test("source review resumes the existing fact confirmation voice session", async () => {
  const factItem = reviewItem();
  factItem.followup_answer.confirmation_type = "fact_resolution";
  const navigations = [];
  global.wx = {
    navigateTo({ url }) { navigations.push(url); },
  };
  const { page } = loadSourceReviewPage({
    listMemoirBookSourceReviewItems: async () => ({ items: [factItem], total: 1 }),
    createMemoirVoiceSession: async () => {
      throw new Error("existing session must be reused");
    },
  }, {
    getMemoirFollowupTask: async () => ({
      id: "task-1",
      status: "in_progress",
      assigned_session_id: "voice-existing-1",
    }),
  });
  const context = pageContext(page);

  await page.onLoad.call(context, { projectId: "project-1", eventId: "event-1" });
  await page.startFactConfirmation.call(context);

  assert.match(navigations[0], /voiceSessionId=voice-existing-1/);
  delete global.wx;
});


test("source review mixed form requires exact non-overlapping original excerpts", () => {
  const { buildSourceReviewConfirmationRequest } = require("../pages/source-review/source-review-form.ts");
  const sourceText = "前半段是一段经历。后半段是在解释为什么这样写。";

  assert.throws(
    () => buildSourceReviewConfirmationRequest(sourceText, "mixed", {
      directNarrativeText: "前半段是一段经历。",
      clarificationText: "",
      factResolutionText: "",
    }),
    /至少拆出两种/,
  );
  assert.throws(
    () => buildSourceReviewConfirmationRequest(sourceText, "mixed", {
      directNarrativeText: "前半段是一段经历。后半段",
      clarificationText: "后半段是在解释为什么这样写。",
      factResolutionText: "",
    }),
    /不能互相重叠/,
  );
  assert.deepEqual(
    buildSourceReviewConfirmationRequest(sourceText, "mixed", {
      directNarrativeText: "前半段是一段经历。",
      clarificationText: "后半段是在解释为什么这样写。",
      factResolutionText: "",
    }),
    {
      followup_answer_kind: "mixed",
      direct_narrative_text: "前半段是一段经历。",
      clarification_text: "后半段是在解释为什么这样写。",
      fact_resolution_text: "",
    },
  );
});
