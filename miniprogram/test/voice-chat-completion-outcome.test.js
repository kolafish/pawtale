require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

const {
  completionOutcomeDialog,
} = require("../pages/voice-chat/voice-chat-completion-outcome.ts");

function outcome(status, patch = {}) {
  return {
    status,
    voice_session_id: "voice-1",
    chapter_title: "界限与邀约",
    route_action: "",
    reason: "",
    message: "测试结果",
    ...patch,
  };
}

test("completion outcome dialog distinguishes incorporated context and ignored answers", () => {
  assert.deepEqual(completionOutcomeDialog(outcome("incorporated")), {
    title: "已经收入章节素材",
    content: "测试结果\n回到章节工作台整理正文后，书稿才会更新。",
    confirmText: "返回章节",
    action: "back",
  });
  assert.deepEqual(completionOutcomeDialog(outcome("context_recorded")), {
    title: "章节说明已经保存",
    content: "测试结果\n这类说明不会直接写成经历，重新整理正文时会用于调整表达。",
    confirmText: "返回章节",
    action: "back",
  });
  assert.deepEqual(
    completionOutcomeDialog(outcome("not_used", { reason: "这是与 AI 的对话。" })),
    {
      title: "这段没有用于书稿",
      content: "测试结果\n原因：这是与 AI 的对话。",
      confirmText: "返回章节重试",
      action: "back",
    },
  );
});

test("completion outcome dialog sends pending answers to review when an event exists", () => {
  assert.deepEqual(
    completionOutcomeDialog(outcome("needs_confirmation", { review_event_id: "event-1" })),
    {
      title: "还需要确认一下",
      content: "测试结果",
      confirmText: "去确认",
      action: "review",
    },
  );
});
