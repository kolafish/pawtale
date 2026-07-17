require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");
const { clearModule, mockModule } = require("./page-harness");

test("completion item verification keeps refreshing until ingestion answers the item", async () => {
  let refreshCalls = 0;
  mockModule("../services/memoir-voice-api.ts", {
    getMemoirCompletionItem: async () => {
      refreshCalls += 1;
      return {
        id: "item-1",
        project_id: "project-1",
        question: "后来发生了什么？",
        status: refreshCalls >= 4 ? "resolved" : "in_progress",
      };
    },
  });
  clearModule("../pages/voice-chat/voice-chat-completion-item-runtime.ts");
  const { VoiceChatCompletionItemRuntime } = require("../pages/voice-chat/voice-chat-completion-item-runtime.ts");
  let state = {
    voiceSessionId: "voice-1",
    projectId: "project-1",
    interviewType: "ai_interview",
    sessionGoal: "book_completion",
    currentCompletionItemId: "item-1",
    completionItem: { id: "item-1", question: "后来发生了什么？", status: "in_progress" },
    completionItemLoading: false,
    completionItemAction: "",
    completionItemError: "",
    completionItemNotice: "",
  };
  const runtime = new VoiceChatCompletionItemRuntime({
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
  const deadline = Date.now() + 500;
  while (state.completionItem.status !== "resolved" && Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 5));
  }

  assert.equal(refreshCalls, 4);
  assert.equal(state.completionItem.status, "resolved");
  assert.equal(state.completionItemNotice, "这项已经补上了");
  assert.equal(state.completionItemError, "");
  runtime.stop();
});

test("unable-to-answer closes only the current completion item", async () => {
  const requests = [];
  mockModule("../services/memoir-voice-api.ts", {
    markMemoirCompletionItemUnableToAnswer: async (projectId, itemId, data) => {
      requests.push({ projectId, itemId, data });
      return { id: itemId, status: "unable_to_answer" };
    },
  });
  clearModule("../pages/voice-chat/voice-chat-completion-item-runtime.ts");
  const { VoiceChatCompletionItemRuntime } = require("../pages/voice-chat/voice-chat-completion-item-runtime.ts");
  let state = {
    voiceSessionId: "voice-1",
    projectId: "project-1",
    interviewType: "ai_interview",
    sessionGoal: "book_completion",
    currentCompletionItemId: "item-1",
    completionItem: { id: "item-1", status: "in_progress" },
    completionItemLoading: false,
    completionItemAction: "",
    completionItemError: "",
    completionItemNotice: "",
  };
  const runtime = new VoiceChatCompletionItemRuntime({
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
  assert.equal(requests[0].itemId, "item-1");
  assert.equal(requests[0].data.note, "用户暂时想不起来这个具体问题");
  assert.equal(state.completionItem.status, "unable_to_answer");
  assert.equal(state.completionItemNotice, "已记为暂时无法回答");
});
