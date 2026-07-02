require("./register-ts");

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const {
  initialVoiceChatData,
  voiceAuthorizationChangePatch,
  voiceAuthorizationConsentPatch,
  voiceCaptureStatsPatch,
  voiceChatErrorPatch,
  voiceChatAutoListenGateSnapshot,
  voiceConnectionStatusPatch,
  voiceGatewayEventPatch,
  voiceSessionDetailPatch,
} = require("../pages/voice-chat/voice-chat-page-state.ts");

test("voice chat initial data keeps the assistant ready for opening narration", () => {
  const first = initialVoiceChatData();
  const second = initialVoiceChatData();

  assert.equal(first.status, "idle");
  assert.equal(first.modeLabel, "慢慢讲");
  assert.equal(first.primaryActionText, "开始说话");
  assert.equal(first.assistantPromptLabel, "小余想听您讲讲");
  assert.equal(first.autoStopEnabled, true);
  assert.equal(first.autoListenEnabled, true);
  assert.deepEqual(first.transcriptLines, []);
  assert.equal(first.openingTopicOptions.length, 5);
  assert.deepEqual(first.openingTopicOptions.map((item) => item.label), [
    "家人",
    "工作",
    "朋友",
    "一个地方",
    "随便聊聊",
  ]);
  assert.notEqual(first.transcriptLines, second.transcriptLines);
  assert.notEqual(first.ttsAlignment, second.ttsAlignment);
  assert.notEqual(first.openingTopicOptions, second.openingTopicOptions);
});

test("voice chat page keeps one main status and a clear recording action", () => {
  const wxml = fs.readFileSync(path.join(__dirname, "../pages/voice-chat/index.wxml"), "utf8");
  const wxss = fs.readFileSync(path.join(__dirname, "../pages/voice-chat/index.wxss"), "utf8");

  assert.match(wxml, /class="topic-panel"/);
  assert.match(wxml, /正在聊：/);
  assert.match(wxml, /class="stage-status/);
  assert.match(wxml, /这一段已经记下来了/);
  assert.match(wxml, /正在帮您整理成文字/);
  assert.match(wxml, /小余正在读问题/);
  assert.match(wxml, /!recording && \(assistantPromptText \|\| followUpLoading \|\| followUpError \|\| canRequestFollowUp\)/);
  assert.match(wxml, /assistantPromptText \|\| \(followUpLoading \? assistantLoadingText/);
  assert.match(wxml, /followUpTTSLoading && assistantPromptText/);
  assert.match(wxml, /小余正在准备第一句话/);
  assert.match(wxml, /小余先问一句，稍等一下。/);
  assert.match(wxml, /今天想先讲哪一段/);
  assert.match(wxml, /openingTopicOptions/);
  assert.match(wxml, /bindtap="chooseOpeningTopic"/);
  assert.match(wxml, /selectedOpeningTopicText/);
  assert.match(wxml, /我来讲了/);
  assert.match(wxml, /说完了，帮我整理/);
  assert.match(wxml, /不知道讲什么，让小余问一句/);
  assert.equal(wxml.includes("assistant-state"), false);
  assert.equal(wxml.includes("status-label"), false);
  assert.match(wxss, /\.mic-button[\s\S]*border-radius: 999rpx/);
  assert.match(wxss, /\.assistant-text[\s\S]*font-size: 32rpx/);
  assert.match(wxss, /\.assistant-topic-button[\s\S]*border-radius: 999rpx/);
});

test("voice chat auto-listen gate snapshot keeps page and computed gate state together", () => {
  assert.deepEqual(
    voiceChatAutoListenGateSnapshot(
      {
        autoListenEnabled: true,
        status: "ready",
        recording: false,
        connecting: false,
        followUpLoading: false,
      },
      {
        hasClient: true,
        pendingAfterPlayback: true,
        canStart: true,
      },
    ),
    {
      hasClient: true,
      autoListenEnabled: true,
      status: "ready",
      recording: false,
      connecting: false,
      followUpLoading: false,
      pendingAfterPlayback: true,
      canStart: true,
    },
  );
});

test("voice chat authorization selectors keep consent text consistent", () => {
  assert.deepEqual(
    voiceAuthorizationConsentPatch({ primaryActionText: "同意后开始说话", recording: false }, true),
    {
      voiceAuthorizationAgreed: true,
      primaryActionText: "开始说话",
      error: "",
      autoListenStateText: "小余说完自动听",
    },
  );
  assert.deepEqual(
    voiceAuthorizationChangePatch({ recording: true, error: "旧错误" }, false),
    {
      voiceAuthorizationAgreed: false,
      recording: false,
      error: "旧错误",
      primaryActionText: "同意后开始说话",
      autoListenStateText: "同意后才会自动收音",
    },
  );
});

test("voice chat session detail selector applies saved and assistant state", () => {
  const data = initialVoiceChatData();
  const patch = voiceSessionDetailPatch({
    session: {
      status: "stopped",
      guidance_mode: "guided_interview",
      current_guidance_module: "person",
    },
    messages: [
      {
        id: "message-user",
        role: "user",
        content_text: "我想讲外婆",
        turn_index: 1,
        created_at: "2026-01-01T00:00:00Z",
      },
      {
        id: "message-assistant",
        role: "assistant",
        content_text: "外婆最常做的一件事是什么？",
        turn_index: 2,
        created_at: "2026-01-01T00:00:01Z",
        audio_file_id: "audio-assistant",
        metadata_json: { expects_answer: true },
      },
    ],
    utterances: [
      {
        id: "utterance-1",
        chat_message_id: "message-user",
        transcript_raw: "我想讲外婆",
        transcript_edited: "",
        created_at: "2026-01-01T00:00:00Z",
        audio_file_id: "audio-user",
      },
    ],
  }, data);

  assert.equal(patch.status, "closed");
  assert.equal(patch.primaryActionText, "已保存");
  assert.equal(patch.currentGuidanceModule, "person");
  assert.equal(patch.latestTranscriptText, "我想讲外婆");
  assert.equal(patch.assistantPromptText, "外婆最常做的一件事是什么？");
  assert.equal(patch.followUpAudioFileId, "audio-assistant");
});

test("voice chat realtime selectors derive capture, gateway, status, and error patches", () => {
  assert.deepEqual(voiceConnectionStatusPatch("listening", { recording: false }), {
    status: "listening",
    statusText: "正在听",
    recording: true,
  });

  const capture = voiceCaptureStatsPatch({
    state: "recording",
    frameCount: 12,
    audioBytesSent: 3072,
    recordingElapsedMs: 61000,
    silentElapsedMs: 4000,
    audioLevel: 0.25,
    gatewayState: "listening",
    autoStopped: true,
    autoStopReason: "no_voice",
  }, {
    autoStopEnabled: true,
    noAnswerPromptAlreadyHandled: false,
  });
  assert.equal(capture.shouldHandleNoAnswerAutoStop, true);
  assert.equal(capture.patch.captureKilobytes, 3);
  assert.equal(capture.patch.captureDurationText, "01:01");
  assert.equal(capture.patch.autoStopStateText, "未听到回答，已暂停收音");
  assert.equal(capture.patch.primaryActionText, "继续说话");

  assert.deepEqual(
    voiceGatewayEventPatch({ type: "audio.ended", state: "finishing" }, { persistenceStateText: "识别中" }),
    {
      gatewayStateText: "识别保存中",
      recording: false,
      primaryActionText: "继续说话",
      persistenceStateText: "正在保存",
    },
  );

  const errorPatch = voiceChatErrorPatch(
    "连接断开",
    { kind: "connection", state: "failed", message: "连接断开" },
    { persistenceStateText: "识别中", recoveryNotice: "" },
  );
  assert.equal(errorPatch.status, "error");
  assert.equal(errorPatch.errorKindText, "连接错误");
  assert.equal(errorPatch.gatewayStateText, "连接异常");
  assert.equal(errorPatch.canReconnect, true);
});
