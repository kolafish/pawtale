require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");
const {
  componentContext,
  loadComponentModule,
  mockModule,
} = require("./page-harness");

function requiredIssue(overrides = {}) {
  return {
    id: "issue-1",
    project_id: "project-1",
    scope: "book",
    issue_type: "sequence",
    channel: "manual_review",
    question: "四段经历的时间顺序是怎样的？",
    reason: "当前章节顺序没有对应时间线。",
    priority: "high",
    related_material_ids: [],
    input_signature: "signature-1",
    status: "open",
    outcome_note: "",
    created_at: "2026-07-28T00:00:00",
    updated_at: "2026-07-28T00:00:00",
    ...overrides,
  };
}

function loadIssueList({ closeIssue } = {}) {
  mockModule("../services/memoir-completion-issue-api.ts", {
    closeMemoirCompletionIssue: closeIssue || (async () => requiredIssue()),
  });
  mockModule("../services/memoir-completion-interview.ts", {
    openMemoirCompletionInterview: async () => {},
  });
  mockModule("../services/memoir-book-completion-api.ts", {
    memoirBookReviewDictationUrl: () =>
      "ws://backend.local/api/memoir/projects/project-1/book-review/dictation",
  });
  mockModule("../services/navigation.ts", { routeAuthError: () => false });
  return loadComponentModule("../components/completion-issue-list/index.ts")
    .component;
}

function issueEvent() {
  return { currentTarget: { dataset: { issueId: "issue-1" } } };
}

test("required book issue opens a real answer editor instead of a confirm modal", () => {
  let modalOpened = false;
  global.wx = {
    showModal() {
      modalOpened = true;
    },
  };
  const component = loadIssueList();
  const context = componentContext(component, {
    projectId: "project-1",
    requiredIssues: [requiredIssue()],
    allowRequiredConfirmation: true,
  });

  context.beginRequiredAnswer.call(context, issueEvent());

  assert.equal(context.data.answeringIssueId, "issue-1");
  assert.equal(context.data.requiredAnswer, "");
  assert.equal(context.data.requiredAnswerReady, false);
  assert.equal(
    context.data.requiredAnswerGatewayUrl,
    "ws://backend.local/api/memoir/projects/project-1/book-review/dictation",
  );
  assert.equal(modalOpened, false);
});

test("required book issue saves the user's exact trimmed explanation", async () => {
  const calls = [];
  const toasts = [];
  global.wx = {
    showToast(options) {
      toasts.push(options);
    },
  };
  const component = loadIssueList({
    closeIssue: async (...args) => calls.push(args),
  });
  const context = componentContext(component, {
    projectId: "project-1",
    requiredIssues: [requiredIssue()],
    allowRequiredConfirmation: true,
  });
  context.beginRequiredAnswer.call(context, issueEvent());
  context.changeRequiredAnswer.call(context, {
    detail: { value: "  先是大学时期，之后才是工作后的经历。  " },
  });

  await context.submitRequiredAnswer.call(context);

  assert.deepEqual(calls, [[
    "project-1",
    "issue-1",
    {
      outcome: "confirmed",
      note: "先是大学时期，之后才是工作后的经历。",
    },
  ]]);
  assert.equal(context.data.answeringIssueId, "");
  assert.equal(context.data.requiredAnswer, "");
  assert.equal(context.data.requiredAnswerReady, false);
  assert.deepEqual(context.events, [{ name: "refresh", detail: undefined }]);
  assert.equal(toasts.at(-1).title, "已处理");
});

test("required book issue rejects an empty explanation without closing", async () => {
  const calls = [];
  const toasts = [];
  global.wx = {
    showToast(options) {
      toasts.push(options);
    },
  };
  const component = loadIssueList({
    closeIssue: async (...args) => calls.push(args),
  });
  const context = componentContext(component, {
    projectId: "project-1",
    requiredIssues: [requiredIssue()],
    allowRequiredConfirmation: true,
  });
  context.beginRequiredAnswer.call(context, issueEvent());
  context.changeRequiredAnswer.call(context, {
    detail: { value: "   " },
  });

  await context.submitRequiredAnswer.call(context);

  assert.deepEqual(calls, []);
  assert.equal(context.data.answeringIssueId, "issue-1");
  assert.equal(context.data.requiredAnswer, "   ");
  assert.equal(context.data.requiredAnswerReady, false);
  assert.equal(toasts.at(-1).title, "请先填写回答");
});

test("required book issue keeps the current draft when another issue is tapped", () => {
  global.wx = {};
  const component = loadIssueList();
  const context = componentContext(component, {
    projectId: "project-1",
    requiredIssues: [
      requiredIssue(),
      requiredIssue({
        id: "issue-2",
        question: "是否保留真实姓名？",
      }),
    ],
    allowRequiredConfirmation: true,
  });
  context.beginRequiredAnswer.call(context, issueEvent());
  context.changeRequiredAnswer.call(context, {
    detail: { value: "先是大学时期，之后是工作后的经历。" },
  });

  context.beginRequiredAnswer.call(context, {
    currentTarget: { dataset: { issueId: "issue-2" } },
  });

  assert.equal(context.data.answeringIssueId, "issue-1");
  assert.equal(
    context.data.requiredAnswer,
    "先是大学时期，之后是工作后的经历。",
  );
  assert.equal(context.data.requiredAnswerReady, true);
});

test("required book issue keeps the explanation available after a save failure", async () => {
  const toasts = [];
  global.wx = {
    showToast(options) {
      toasts.push(options);
    },
  };
  const component = loadIssueList({
    closeIssue: async () => {
      throw new Error("网络不可用");
    },
  });
  const context = componentContext(component, {
    projectId: "project-1",
    requiredIssues: [requiredIssue()],
    allowRequiredConfirmation: true,
  });
  context.beginRequiredAnswer.call(context, issueEvent());
  context.changeRequiredAnswer.call(context, {
    detail: { value: "顺序是大学、第一份工作、创业、退休后。" },
  });

  await context.submitRequiredAnswer.call(context);

  assert.equal(context.data.answeringIssueId, "issue-1");
  assert.equal(
    context.data.requiredAnswer,
    "顺序是大学、第一份工作、创业、退休后。",
  );
  assert.equal(context.data.requiredAnswerReady, true);
  assert.equal(context.data.busyIssueId, "");
  assert.equal(toasts.at(-1).title, "网络不可用");
});
