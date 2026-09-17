require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");
const {
  finishSessionSavedPatch,
  finishSessionStartPatch,
  liveTranscriptScrollTop,
  noAnswerPromptFailedPatch,
  noAnswerPromptPlan,
  noAnswerPromptQueuedPatch,
  voiceFinalTranscriptResult,
  voiceClientUnavailableForRecording,
  voiceLiveTranscriptPatch,
  voiceRecordingStartRequestedPatch,
  voiceRecordingStartedPatch,
  voiceRecordingUnavailablePatch,
  voiceTranscriptFollowLatestPatch,
  voiceTranscriptReviewStartedPatch,
} = require("../components/voice-session/voice-chat-recording-flow.ts");

test("voice recording flow detects when recorder must reconnect first", () => {
  assert.equal(voiceClientUnavailableForRecording(false, "ready"), true);
  assert.equal(voiceClientUnavailableForRecording(true, "closed"), true);
  assert.equal(voiceClientUnavailableForRecording(true, "error"), true);
  assert.equal(voiceClientUnavailableForRecording(true, "ready"), false);
});

test("voice recording flow derives recording start and unavailable patches", () => {
  assert.deepEqual(voiceRecordingStartRequestedPatch(), {
    recordingStarting: true,
    error: "",
  });
  assert.deepEqual(voiceRecordingStartedPatch(true), {
    recording: true,
    recordingStarting: false,
    error: "",
    liveText: "",
    liveCharCount: 0,
    liveScrollClass: "live-scroll",
    liveScrollTop: 0,
    liveTranscriptFollowing: true,
    liveReviewStartCharCount: 0,
    selectedOpeningTopicText: "",
    primaryActionText: "暂停收音",
    autoListenStateText: "小余说完，正在听您说",
  });
  assert.deepEqual(voiceRecordingUnavailablePatch(false, "原状态"), {
    error: "语音未就绪，请重新连接",
    recordingStarting: false,
    status: "error",
    statusText: "异常",
    primaryActionText: "重新连接",
    canReconnect: true,
    autoListenStateText: "原状态",
  });
});

test("voice recording flow derives live transcript content without guessing its scroll position", () => {
  const longPartial = "这是一段很长的正常访谈实时识别文字，".repeat(6);

  const firstPatch = voiceLiveTranscriptPatch(longPartial);
  assert.equal(firstPatch.liveText, longPartial);
  assert.equal(firstPatch.liveCharCount, longPartial.length);
  assert.equal(firstPatch.liveScrollClass, "live-scroll long");
  assert.equal(firstPatch.liveScrollTop, undefined);
  assert.equal(firstPatch.persistenceStateText, "识别中");

  const emptyPatch = voiceLiveTranscriptPatch("");
  assert.equal(emptyPatch.liveScrollClass, "live-scroll");
  assert.equal(emptyPatch.liveScrollTop, 0);
  assert.equal(emptyPatch.liveTranscriptFollowing, true);
});

test("live transcript scroll follows rendered line height and handles ASR rewrites", () => {
  assert.equal(liveTranscriptScrollTop(390, 390), 0);
  assert.equal(liveTranscriptScrollTop(467.2, 390), 78);
  assert.equal(liveTranscriptScrollTop(545, 390), 155);
  assert.equal(liveTranscriptScrollTop(467, 390), 77);
  assert.equal(liveTranscriptScrollTop(300, 390), 0);
  assert.equal(liveTranscriptScrollTop(Number.NaN, 390), 0);
  assert.equal(liveTranscriptScrollTop(500, 0), 0);
});

test("voice recording flow pauses review and explicitly returns to the newest caption", () => {
  assert.deepEqual(voiceTranscriptReviewStartedPatch({
    liveCharCount: 88,
    liveTranscriptFollowing: true,
  }), {
    liveTranscriptFollowing: false,
    liveReviewStartCharCount: 88,
  });
  assert.deepEqual(voiceTranscriptFollowLatestPatch(), {
    liveTranscriptFollowing: true,
    liveReviewStartCharCount: 0,
  });
});

test("voice recording flow derives final transcript result", () => {
  const result = voiceFinalTranscriptResult(
    {
      type: "asr.final",
      text: "最终保存的一句话。",
      message_id: "message-1",
      utterance_id: "utterance-1",
      transcript_version_id: "version-1",
    },
    {
      transcriptLines: [],
      guidanceMode: "guided_interview",
      lastFollowUpSourceMessageId: "",
    },
    "fallback-1",
  );

  assert.ok(result);
  assert.equal(result.line.id, "message-1");
  assert.equal(result.line.text, "最终保存的一句话。");
  assert.deepEqual(result.transcriptLines.map((line) => line.id), ["message-1"]);
  assert.equal(result.patch.liveText, "");
  assert.equal(result.patch.liveCharCount, 0);
  assert.equal(result.patch.liveScrollClass, "live-scroll");
  assert.equal(result.patch.liveScrollTop, 0);
  assert.equal(result.patch.liveTranscriptFollowing, true);
  assert.equal(result.patch.liveReviewStartCharCount, 0);
  assert.equal(result.patch.persistenceStateText, "已保存");
  assert.equal(result.patch.followUpLoading, false);
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
    recordingStarting: false,
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
