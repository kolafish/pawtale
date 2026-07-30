require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");
const { mockMemoirApiModules, clearModule, mockModule } = require("./page-harness");

function issue(status, patch = {}) {
  return {
    id: "issue-1",
    project_id: "project-1",
    scope: "section",
    section_id: "section-1",
    chapter_id: "chapter-1",
    issue_type: "context",
    channel: "interview",
    question: "后来发生了什么？",
    reason: "读者还不了解后续。",
    priority: "high",
    related_material_ids: [],
    input_signature: "signature-1",
    status,
    outcome: null,
    outcome_note: "",
    created_at: "2026-07-18T00:00:00Z",
    updated_at: "2026-07-18T00:00:00Z",
    ...patch,
  };
}

function runtimeState() {
  return {
    projectId: "project-1",
    sessionGoal: "book_completion",
    currentCompletionIssueId: "issue-1",
    completionIssue: issue("in_progress"),
    completionIssueLoading: false,
    completionIssueAction: "",
    completionIssueError: "",
    completionIssueNotice: "",
  };
}

test("completion issue verification stops when the answer closes the issue", async () => {
  let refreshCalls = 0;
  mockMemoirApiModules({
    listMemoirCompletionIssues: async () => {
      refreshCalls += 1;
      const current = refreshCalls >= 3
        ? issue("closed", { outcome: "answered" })
        : issue("in_progress");
      return { project_id: "project-1", current_issue: current, issues: [current] };
    },
  });
  clearModule("../pages/voice-chat/voice-chat-completion-issue-runtime.ts");
  const {
    VoiceChatCompletionIssueRuntime,
  } = require("../pages/voice-chat/voice-chat-completion-issue-runtime.ts");
  let state = runtimeState();
  const runtime = new VoiceChatCompletionIssueRuntime({
    readState: () => state,
    applyPatch: (patch) => {
      state = { ...state, ...patch };
    },
    isSuspended: () => false,
    cancelAssistant() {},
    refreshIntervalMs: 1,
  });

  runtime.startVerification();
  const deadline = Date.now() + 500;
  while (state.completionIssue.status !== "closed" && Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 5));
  }

  assert.equal(refreshCalls, 3);
  assert.equal(state.completionIssue.status, "closed");
  assert.equal(state.completionIssue.outcome, "answered");
  assert.equal(state.completionIssueNotice, "回答已经保存");
  assert.equal(state.completionIssueError, "");
  runtime.stop();
});

test("not remembered closes only the current completion issue", async () => {
  const requests = [];
  mockMemoirApiModules({
    closeMemoirCompletionIssue: async (projectId, issueId, data) => {
      requests.push({ projectId, issueId, data });
      return issue("closed", { outcome: data.outcome });
    },
  });
  clearModule("../pages/voice-chat/voice-chat-completion-issue-runtime.ts");
  const {
    VoiceChatCompletionIssueRuntime,
  } = require("../pages/voice-chat/voice-chat-completion-issue-runtime.ts");
  let state = runtimeState();
  const runtime = new VoiceChatCompletionIssueRuntime({
    readState: () => state,
    applyPatch: (patch) => {
      state = { ...state, ...patch };
    },
    isSuspended: () => false,
    cancelAssistant() {},
  });

  await runtime.close("not_remembered");

  assert.deepEqual(requests, [{
    projectId: "project-1",
    issueId: "issue-1",
    data: {
      outcome: "not_remembered",
      note: "用户暂时想不起来这个问题",
    },
  }]);
  assert.equal(state.completionIssue.status, "closed");
  assert.equal(state.completionIssueNotice, "已记下您暂时想不起来");
});
