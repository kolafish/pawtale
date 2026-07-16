require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");
const { clearModule, mockModule } = require("./page-harness");

test("completion task verification keeps refreshing until ingestion answers the task", async () => {
  let refreshCalls = 0;
  mockModule("../services/memoir-voice-api.ts", {
    getMemoirFollowupTask: async () => {
      refreshCalls += 1;
      return {
        id: "task-1",
        project_id: "project-1",
        question: "后来发生了什么？",
        status: refreshCalls >= 4 ? "answered" : "in_progress",
      };
    },
  });
  clearModule("../pages/voice-chat/voice-chat-completion-task-runtime.ts");
  const { VoiceChatCompletionTaskRuntime } = require("../pages/voice-chat/voice-chat-completion-task-runtime.ts");
  let state = {
    voiceSessionId: "voice-1",
    projectId: "project-1",
    interviewType: "ai_interview",
    sessionGoal: "book_completion",
    currentFollowupTaskId: "task-1",
    completionTask: { id: "task-1", question: "后来发生了什么？", status: "in_progress" },
    completionTaskLoading: false,
    completionTaskAction: "",
    completionTaskError: "",
    completionTaskNotice: "",
  };
  const runtime = new VoiceChatCompletionTaskRuntime({
    readState: () => state,
    applyPatch: (patch) => {
      state = { ...state, ...patch };
    },
    isSuspended: () => false,
    cancelAssistant() {},
    requestAssistant() {},
    refreshIntervalMs: 1,
  });

  runtime.startVerification();
  await new Promise((resolve) => setTimeout(resolve, 30));

  assert.equal(refreshCalls, 4);
  assert.equal(state.completionTask.status, "answered");
  assert.equal(state.completionTaskNotice, "这项已经补上了");
  assert.equal(state.completionTaskError, "");
  runtime.stop();
});

test("unable-to-answer closes only the current follow-up task", async () => {
  const requests = [];
  mockModule("../services/memoir-voice-api.ts", {
    markMemoirFollowupTaskUnableToAnswer: async (projectId, taskId, data) => {
      requests.push({ projectId, taskId, data });
      return { id: taskId, status: "unable_to_answer" };
    },
  });
  clearModule("../pages/voice-chat/voice-chat-completion-task-runtime.ts");
  const { VoiceChatCompletionTaskRuntime } = require("../pages/voice-chat/voice-chat-completion-task-runtime.ts");
  let state = {
    voiceSessionId: "voice-1",
    projectId: "project-1",
    interviewType: "ai_interview",
    sessionGoal: "book_completion",
    currentFollowupTaskId: "task-1",
    completionTask: { id: "task-1", status: "in_progress" },
    completionTaskLoading: false,
    completionTaskAction: "",
    completionTaskError: "",
    completionTaskNotice: "",
  };
  const runtime = new VoiceChatCompletionTaskRuntime({
    readState: () => state,
    applyPatch: (patch) => {
      state = { ...state, ...patch };
    },
    isSuspended: () => false,
    cancelAssistant() {},
    requestAssistant() {},
  });

  await runtime.applyUnableToAnswer("unable_to_answer");

  assert.equal(requests.length, 1);
  assert.equal(requests[0].projectId, "project-1");
  assert.equal(requests[0].taskId, "task-1");
  assert.equal(requests[0].data.note, "用户暂时想不起来这个具体问题");
  assert.equal(state.completionTask.status, "unable_to_answer");
  assert.equal(state.completionTaskNotice, "已记为暂时无法回答");
});

test("stop asking keeps existing book material explicitly unaffected", async () => {
  const requests = [];
  mockModule("../services/memoir-voice-api.ts", {
    stopAskingMemoirFollowupTask: async (projectId, taskId, data) => {
      requests.push({ projectId, taskId, data });
      return { id: taskId, status: "closed_without_answer" };
    },
  });
  clearModule("../pages/voice-chat/voice-chat-completion-task-runtime.ts");
  const { VoiceChatCompletionTaskRuntime } = require("../pages/voice-chat/voice-chat-completion-task-runtime.ts");
  let state = {
    voiceSessionId: "voice-1",
    projectId: "project-1",
    interviewType: "ai_interview",
    sessionGoal: "book_completion",
    currentFollowupTaskId: "task-1",
    completionTask: { id: "task-1", status: "in_progress" },
    completionTaskLoading: false,
    completionTaskAction: "",
    completionTaskError: "",
    completionTaskNotice: "",
  };
  const runtime = new VoiceChatCompletionTaskRuntime({
    readState: () => state,
    applyPatch: (patch) => {
      state = { ...state, ...patch };
    },
    isSuspended: () => false,
    cancelAssistant() {},
    requestAssistant() {},
  });

  await runtime.stopAsking();

  assert.equal(requests.length, 1);
  assert.equal(requests[0].data.note, "用户明确选择不再询问这个具体问题");
  assert.equal(state.completionTask.status, "closed_without_answer");
  assert.equal(state.completionTaskNotice, "以后不再问这个问题，已有内容不受影响");
});
