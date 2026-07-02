require("./register-ts");

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const {
  initialVoiceChatData,
  voiceChatAutoListenGateSnapshot,
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
