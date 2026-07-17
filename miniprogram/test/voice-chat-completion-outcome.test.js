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

test("completion outcome dialog distinguishes incorporated and unused answers", () => {
  assert.deepEqual(completionOutcomeDialog(outcome("incorporated")), {
    title: "已经收入章节素材",
    content: "测试结果\n回到章节工作台整理正文后，书稿才会更新。",
    confirmText: "返回章节",
    action: "back",
  });
  assert.deepEqual(completionOutcomeDialog(outcome("not_used")), {
      title: "这段没有用于书稿",
      content: "测试结果",
      confirmText: "返回章节重试",
      action: "back",
    });
});
