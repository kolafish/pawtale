require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");
const {
  finishSessionSavedPatch,
  finishSessionStartPatch,
  noAnswerPromptFailedPatch,
  noAnswerPromptPlan,
  noAnswerPromptQueuedPatch,
  voiceClientUnavailableForRecording,
  voiceRecordingStartedPatch,
  voiceRecordingUnavailablePatch,
} = require("../pages/voice-chat/voice-chat-recording-flow.ts");

test("voice recording flow detects when recorder must reconnect first", () => {
  assert.equal(voiceClientUnavailableForRecording(false, "ready"), true);
  assert.equal(voiceClientUnavailableForRecording(true, "closed"), true);
  assert.equal(voiceClientUnavailableForRecording(true, "error"), true);
  assert.equal(voiceClientUnavailableForRecording(true, "ready"), false);
});

test("voice recording flow derives recording start and unavailable patches", () => {
  assert.deepEqual(voiceRecordingStartedPatch(true), {
    recording: true,
    error: "",
    liveText: "",
    liveCharCount: 0,
    selectedOpeningTopicText: "",
    primaryActionText: "暂停收音",
    autoListenStateText: "小余说完，正在听您说",
  });
  assert.deepEqual(voiceRecordingUnavailablePatch(false, "原状态"), {
    error: "语音未就绪，请重新连接",
    status: "error",
    statusText: "异常",
    primaryActionText: "重新连接",
    canReconnect: true,
    autoListenStateText: "原状态",
  });
});

test("voice recording flow plans no-answer prompts", () => {
  assert.deepEqual(noAnswerPromptPlan(0, 2), { attempt: 1, autoListenAfterPlayback: true });
  assert.deepEqual(noAnswerPromptPlan(1, 2), { attempt: 2, autoListenAfterPlayback: false });
  assert.equal(noAnswerPromptQueuedPatch(false).autoListenStateText, "已暂停，可点继续说话");
  assert.deepEqual(noAnswerPromptFailedPatch(true, "生成失败"), {
    followUpError: "生成失败",
    persistenceStateText: "确认失败",
    autoListenStateText: "没听到回答，可点继续说话",
    canRequestFollowUp: true,
  });
});

test("voice recording flow derives finish session patches", () => {
  assert.deepEqual(finishSessionStartPatch(true), {
    finishingSession: true,
    error: "",
    recording: false,
    status: "stopping",
    statusText: "保存中",
    primaryActionText: "保存中",
    persistenceStateText: "正在保存最后一句",
  });
  assert.deepEqual(finishSessionSavedPatch(), {
    recording: false,
    primaryActionText: "已保存",
    status: "closed",
    statusText: "已保存",
    persistenceStateText: "正在同步",
  });
});
