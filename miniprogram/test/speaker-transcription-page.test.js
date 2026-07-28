require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");
const { clearModule, loadPageModule, mockModule, pageContext } = require("./page-harness");

function loadSpeakerTranscriptionPage({
  accessibilityExports = {},
  proofreadExports = {},
  privacyExports = null,
  transcribeExports = null,
} = {}) {
  mockModule("../services/accessibility-settings.ts", {
    readAccessibilitySettings: () => ({
      largeTextEnabled: false,
      autoBargeInEnabled: true,
    }),
    ...accessibilityExports,
  });
  mockModule("../services/speaker-transcript-proofreading.ts", {
    proofreadSpeakerTranscriptLine: async (text, contextLines = []) => ({
      text,
      proofread_text: text,
      changed: false,
      provider: "test",
      context_line_count: contextLines.length,
    }),
    ...proofreadExports,
  });
  if (privacyExports) {
    mockModule("../services/wechat-privacy.ts", {
      ensureRecorderPrivacyAuthorization: async () => {},
      ...privacyExports,
    });
  } else {
    clearModule("../services/wechat-privacy.ts");
  }
  if (transcribeExports) {
    mockModule("../services/speaker-transcription.ts", {
      SpeakerTranscriptionClient: class SpeakerTranscriptionClient {
        async start() {}
        stop() {}
        close() {}
      },
      speakerTitle: (speakerId, speakerIndex) => (
        speakerId === "speaker_unknown" ? "未分辨说话人" : `说话人 ${speakerIndex}`
      ),
      ...transcribeExports,
    });
  } else {
    clearModule("../services/speaker-transcription.ts");
  }
  clearModule("../pages/speaker-transcription/speaker-transcription-text-reconciliation.ts");
  clearModule("../pages/speaker-transcription/speaker-transcription-reducer.ts");
  clearModule("../pages/speaker-transcription/index.ts");
  return loadPageModule("../pages/speaker-transcription/index.ts");
}

function flushPromises() {
  return new Promise((resolve) => setImmediate(resolve));
}

test("speaker transcription page shows partial and final text in conversation flow", () => {
  const { page } = loadSpeakerTranscriptionPage();
  const context = pageContext(page);

  page.handleSpeakerEvent.call(context, {
    type: "speaker.partial",
    speaker_id: "speaker_1",
    text: "你小时候住在哪里？",
    start_ms: 0,
    end_ms: 1200,
  });

  assert.equal(context.data.activePartial.visible, true);
  assert.equal(context.data.activePartial.speakerTitle, "说话人 1");
  assert.equal(context.data.activePartial.text, "你小时候住在哪里？");
  assert.equal(context.data.conversationScrollTarget, "active-partial-card");
  assert.equal(context.data.showEmpty, false);
  assert.equal(context.data.controlPanelClass, "control-panel compact");
  assert.equal(context.data.showFullControls, false);

  page.handleSpeakerEvent.call(context, {
    type: "speaker.final",
    speaker_id: "speaker_2",
    speaker_gender: "female",
    segment_id: "seg-2",
    text: "住在厂里的家属院。",
    start_ms: 1500,
    end_ms: 3600,
    confidence: 0.86,
  });

  assert.equal(context.data.activePartial.visible, false);
  assert.equal(context.data.conversationLines.length, 1);
  assert.equal(context.data.conversationLines[0].speakerTitle, "女1");
  assert.equal(context.data.conversationLines[0].speakerBadgeClass, "speaker-badge tone-1");
  assert.equal(context.data.conversationLines[0].text, "住在厂里的家属院。");
  assert.equal(context.data.conversationLines[0].metaText, "0:01-0:03 · 置信度 86%");
  assert.equal(context.data.conversationLines[0].lineClass, "line-item latest tone-1");
  assert.equal(context.data.conversationLines[0].textClass, "line-text latest");
  assert.equal(context.data.conversationScrollTarget, context.data.conversationLines[0].domId);
  assert.equal(context.data.showEmpty, false);
  assert.equal(context.data.clearDisabled, false);
});

test("speaker transcription page requires recorder privacy authorization before connecting", async () => {
  let clientConstructed = false;
  const { page } = loadSpeakerTranscriptionPage({
    privacyExports: {
      ensureRecorderPrivacyAuthorization: async () => {
        throw new Error("请先同意微信小程序隐私保护指引后再开始录音。");
      },
    },
    transcribeExports: {
      SpeakerTranscriptionClient: class SpeakerTranscriptionClient {
        constructor() {
          clientConstructed = true;
        }
      },
    },
  });
  const context = pageContext(page);

  await page.toggleRecording.call(context);

  assert.equal(clientConstructed, false);
  assert.equal(context.data.recording, false);
  assert.equal(context.data.busy, false);
  assert.equal(context.data.statusText, "出现错误");
  assert.equal(context.data.error, "请先同意微信小程序隐私保护指引后再开始录音。");
});

test("speaker transcription page ignores privacy results after unload", async () => {
  let rejectAuthorization;
  const { page } = loadSpeakerTranscriptionPage({
    privacyExports: {
      ensureRecorderPrivacyAuthorization: () => new Promise((_resolve, reject) => {
        rejectAuthorization = reject;
      }),
    },
  });
  const context = pageContext(page);

  const start = page.toggleRecording.call(context);
  await Promise.resolve();
  page.onUnload.call(context);
  rejectAuthorization(new Error("页面退出后的授权结果"));
  await start;

  assert.equal(context.data.statusText, "未开始");
  assert.equal(context.data.error, "");
});

test("speaker transcription page ignores callbacks from a previous client", async () => {
  const clients = [];
  class FakeSpeakerTranscriptionClient {
    constructor(callbacks) {
      this.callbacks = callbacks;
      clients.push(this);
    }

    async start() {
      this.callbacks.onStatus("listening");
    }

    stop() {}

    close() {}
  }
  const { page } = loadSpeakerTranscriptionPage({
    privacyExports: {},
    transcribeExports: { SpeakerTranscriptionClient: FakeSpeakerTranscriptionClient },
  });
  const context = pageContext(page);

  await page.toggleRecording.call(context);
  clients[0].callbacks.onSpeakerEvent({
    type: "speaker.final",
    speaker_id: "speaker_1",
    segment_id: "shared-segment",
    text: "第一轮的结果。",
  });
  clients[0].callbacks.onStatus("closed");
  await page.toggleRecording.call(context);

  clients[0].callbacks.onSpeakerEvent({
    type: "speaker.final",
    speaker_id: "speaker_1",
    segment_id: "stale",
    text: "上一轮迟到的结果。",
  });
  clients[1].callbacks.onSpeakerEvent({
    type: "speaker.final",
    speaker_id: "speaker_1",
    segment_id: "shared-segment",
    text: "当前这一轮的结果。",
  });

  const currentTexts = context.data.conversationLines.map((line) => line.text);
  assert.equal(currentTexts.length, 2);
  assert.equal(currentTexts[0], "当前这一轮的结果。");
  assert.equal(currentTexts[1], "第一轮的结果。");
});

test("speaker transcription page formats segment meta as Beijing occurrence time when available", () => {
  const { page } = loadSpeakerTranscriptionPage();
  const context = pageContext(page);

  page.handleSpeakerEvent.call(context, {
    type: "speaker.final",
    speaker_id: "speaker_1",
    segment_id: "seg-1",
    text: "按实际发生时间显示。",
    occurred_start_at_ms: Date.UTC(2026, 6, 7, 5, 30, 44),
    occurred_end_at_ms: Date.UTC(2026, 6, 7, 5, 30, 47),
    start_ms: 44_000,
    end_ms: 47_000,
    confidence: 0.86,
  });

  assert.equal(context.data.conversationLines[0].metaText, "13:30:44-13:30:47 · 置信度 86%");
});

test("speaker transcription page removes finalized text from live unknown partial", () => {
  const { page } = loadSpeakerTranscriptionPage();
  const context = pageContext(page);

  page.handleSpeakerEvent.call(context, {
    type: "speaker.partial",
    speaker_id: "speaker_unknown",
    segment_id: "live-current",
    text: "第一句已经稳定。第二句还在实时说",
  });
  page.handleSpeakerEvent.call(context, {
    type: "speaker.final",
    speaker_id: "speaker_1",
    segment_id: "seg-1",
    text: "第一句已经稳定。",
    start_ms: 0,
    end_ms: 1800,
  });

  assert.equal(context.data.conversationLines.length, 1);
  assert.equal(context.data.activePartial.visible, true);
  assert.equal(context.data.activePartial.text, "第二句还在实时说");
  assert.equal(context.data.conversationScrollTarget, "active-partial-card");
});

test("speaker transcription page clears live unknown partial when final text covers it", () => {
  const { page } = loadSpeakerTranscriptionPage();
  const context = pageContext(page);

  page.handleSpeakerEvent.call(context, {
    type: "speaker.partial",
    speaker_id: "speaker_unknown",
    segment_id: "live-current",
    text: "第二句已经稳定。",
  });
  page.handleSpeakerEvent.call(context, {
    type: "speaker.final",
    speaker_id: "speaker_2",
    segment_id: "seg-2",
    text: "第二句已经稳定。",
  });

  assert.equal(context.data.activePartial.visible, false);
  assert.equal(context.data.conversationScrollTarget, context.data.conversationLines[0].domId);
});

test("speaker transcription page does not re-add finalized speaker text to cumulative unknown preview", () => {
  const { page } = loadSpeakerTranscriptionPage();
  const context = pageContext(page);
  const cumulativeLiveText = "分糖的？七分糖。认出来了吗？嗯。认出来了，是认出来了，有点问题。";

  page.handleSpeakerEvent.call(context, {
    type: "speaker.partial",
    speaker_id: "speaker_unknown",
    segment_id: "live-current",
    text: cumulativeLiveText,
  });
  page.handleSpeakerEvent.call(context, {
    type: "speaker.final",
    speaker_id: "speaker_9",
    segment_id: "seg-9",
    text: "分糖的？七分糖。",
    start_ms: 83_000,
    end_ms: 84_000,
  });

  assert.equal(context.data.activePartial.visible, true);
  assert.equal(context.data.activePartial.text, "认出来了吗？嗯。认出来了，是认出来了，有点问题。");

  page.handleSpeakerEvent.call(context, {
    type: "speaker.final",
    speaker_id: "speaker_3",
    segment_id: "seg-3",
    text: "认出来了，是认出来了，有点问题。",
    start_ms: 84_000,
    end_ms: 87_000,
  });

  assert.equal(context.data.activePartial.visible, true);
  assert.equal(context.data.activePartial.text, "认出来了吗？嗯。");

  page.handleSpeakerEvent.call(context, {
    type: "speaker.partial",
    speaker_id: "speaker_unknown",
    segment_id: "live-current",
    text: cumulativeLiveText,
  });

  assert.equal(context.data.activePartial.visible, true);
  assert.equal(context.data.activePartial.text, "认出来了吗？嗯。");
  assert.deepEqual(context.data.conversationLines.map((line) => line.text), [
    "认出来了，是认出来了，有点问题。",
    "分糖的？七分糖。",
  ]);
});

test("speaker transcription page clears stale unknown preview when two ASR streams disagree on a short utterance", () => {
  const { page } = loadSpeakerTranscriptionPage();
  const context = pageContext(page);
  const liveText = "然后这道吗？应该就是第四道了哈。";

  page.handleSpeakerEvent.call(context, {
    type: "speaker.partial",
    speaker_id: "speaker_unknown",
    segment_id: "live-current",
    text: liveText,
  });

  assert.equal(context.data.activePartial.visible, true);
  assert.equal(context.data.activePartial.text, liveText);

  page.handleSpeakerEvent.call(context, {
    type: "speaker.final",
    speaker_id: "speaker_2",
    segment_id: "seg-2",
    text: "现在应该是第八道了哈。",
    start_ms: 44_000,
    end_ms: 47_000,
  });

  assert.equal(context.data.conversationLines[0].text, "现在应该是第八道了哈。");
  assert.equal(context.data.activePartial.visible, false);
  assert.equal(context.data.conversationScrollTarget, context.data.conversationLines[0].domId);

  page.handleSpeakerEvent.call(context, {
    type: "speaker.partial",
    speaker_id: "speaker_unknown",
    segment_id: "live-current",
    text: liveText,
  });

  assert.equal(context.data.activePartial.visible, false);
});

test("speaker transcription page removes only the stale time-overlapping sentence window", () => {
  const { page } = loadSpeakerTranscriptionPage();
  const context = pageContext(page);
  const liveText = "然后这道吗？应该就是第四道了哈。第五次现在还在继续说。";

  page.handleSpeakerEvent.call(context, {
    type: "speaker.partial",
    speaker_id: "speaker_unknown",
    segment_id: "live-current",
    text: liveText,
    start_ms: 44_000,
    end_ms: 50_000,
  });
  page.handleSpeakerEvent.call(context, {
    type: "speaker.final",
    speaker_id: "speaker_2",
    segment_id: "seg-2",
    text: "现在应该是第八道了哈。",
    start_ms: 44_000,
    end_ms: 47_000,
  });

  assert.equal(context.data.activePartial.visible, true);
  assert.equal(context.data.activePartial.text, "第五次现在还在继续说。");

  page.handleSpeakerEvent.call(context, {
    type: "speaker.partial",
    speaker_id: "speaker_unknown",
    segment_id: "live-current",
    text: liveText,
    start_ms: 44_000,
    end_ms: 50_000,
  });

  assert.equal(context.data.activePartial.visible, true);
  assert.equal(context.data.activePartial.text, "第五次现在还在继续说。");
});

test("speaker transcription page keeps repeated words when final line is outside the partial time range", () => {
  const { page } = loadSpeakerTranscriptionPage();
  const context = pageContext(page);

  page.handleSpeakerEvent.call(context, {
    type: "speaker.final",
    speaker_id: "speaker_1",
    segment_id: "seg-1",
    text: "好。",
    start_ms: 0,
    end_ms: 1000,
  });
  page.handleSpeakerEvent.call(context, {
    type: "speaker.partial",
    speaker_id: "speaker_unknown",
    segment_id: "live-current",
    text: "好。",
    start_ms: 7000,
    end_ms: 8000,
  });

  assert.equal(context.data.activePartial.visible, true);
  assert.equal(context.data.activePartial.text, "好。");
});

test("speaker transcription page re-cleans active unknown preview after proofread changes final text", async () => {
  const { page } = loadSpeakerTranscriptionPage({
    proofreadExports: {
      proofreadSpeakerTranscriptLine: async () => ({
        proofread_text: "第四道残留。",
      }),
    },
  });
  const context = pageContext(page);

  page.handleSpeakerEvent.call(context, {
    type: "speaker.partial",
    speaker_id: "speaker_unknown",
    segment_id: "live-current",
    text: "第四道残留。后面新的话。",
    start_ms: 10_000,
    end_ms: 15_000,
  });
  page.handleSpeakerEvent.call(context, {
    type: "speaker.final",
    speaker_id: "speaker_2",
    segment_id: "seg-2",
    text: "识别得不准。",
    start_ms: 10_000,
    end_ms: 12_000,
  });

  assert.equal(context.data.activePartial.text, "第四道残留。后面新的话。");

  await flushPromises();

  assert.equal(context.data.conversationLines[0].text, "第四道残留。");
  assert.equal(context.data.activePartial.visible, true);
  assert.equal(context.data.activePartial.text, "后面新的话。");
});

test("speaker transcription page keeps unrelated unknown preview when final text is not the same utterance", () => {
  const { page } = loadSpeakerTranscriptionPage();
  const context = pageContext(page);

  page.handleSpeakerEvent.call(context, {
    type: "speaker.partial",
    speaker_id: "speaker_unknown",
    segment_id: "live-current",
    text: "第五次现在还在继续说。",
  });
  page.handleSpeakerEvent.call(context, {
    type: "speaker.final",
    speaker_id: "speaker_2",
    segment_id: "seg-2",
    text: "第四次已经稳定了。",
  });

  assert.equal(context.data.activePartial.visible, true);
  assert.equal(context.data.activePartial.text, "第五次现在还在继续说。");
});

test("speaker transcription page keeps final text newest-first with gender labels", async () => {
  const { page } = loadSpeakerTranscriptionPage();
  const context = pageContext(page);

  page.handleSpeakerEvent.call(context, {
    type: "speaker.final",
    speaker_id: "speaker_1",
    speaker_gender: "male",
    segment_id: "seg-1",
    text: "我先说第一句。",
    start_ms: 0,
    end_ms: 1200,
  });
  await flushPromises();
  page.handleSpeakerEvent.call(context, {
    type: "speaker.final",
    speaker_id: "speaker_2",
    speaker_gender: "female",
    segment_id: "seg-2",
    text: "我接着说第二句。",
    start_ms: 1300,
    end_ms: 2600,
  });
  await flushPromises();
  page.handleSpeakerEvent.call(context, {
    type: "speaker.final",
    speaker_id: "speaker_1",
    segment_id: "seg-3",
    text: "我再补第三句。",
    start_ms: 2700,
    end_ms: 3900,
  });
  await flushPromises();

  assert.deepEqual(context.data.conversationLines.map((line) => line.speakerTitle), [
    "男1",
    "女1",
    "男1",
  ]);
  assert.deepEqual(context.data.conversationLines.map((line) => line.text), [
    "我再补第三句。",
    "我接着说第二句。",
    "我先说第一句。",
  ]);
  assert.deepEqual(context.data.conversationLines.map((line) => line.textClass), [
    "line-text latest",
    "line-text history",
    "line-text history",
  ]);
});

test("speaker transcription page shows provider diarization diagnostics", () => {
  const { page } = loadSpeakerTranscriptionPage();
  const context = pageContext(page);

  page.handleDiarizationStatus.call(context, {
    type: "speaker.diarization",
    status: "missing_provider_labels",
    message: "当前 ASR 返回结果没有说话人标签，暂时只能标成未分辨说话人。",
  });

  assert.equal(context.data.showDiarizationNotice, true);
  assert.equal(context.data.diarizationNoticeClass, "diarization-notice warning");
  assert.equal(context.data.diarizationNotice, "当前 ASR 返回结果没有说话人标签，暂时只能标成未分辨说话人。");

  page.handleDiarizationStatus.call(context, {
    type: "speaker.diarization",
    status: "available",
  });

  assert.equal(context.data.diarizationNoticeClass, "diarization-notice ok");
  assert.equal(context.data.diarizationNotice, "已收到说话人标签，会按不同人标注显示。");
});

test("speaker transcription page keeps only new text from cumulative final results", () => {
  const { page } = loadSpeakerTranscriptionPage();
  const context = pageContext(page);

  page.handleSpeakerEvent.call(context, {
    type: "speaker.final",
    speaker_id: "speaker_unknown",
    segment_id: "seg-1",
    text: "来，我们来告一下嘛，看能不能看清楚哈。",
    start_ms: 0,
    end_ms: 6000,
  });
  page.handleSpeakerEvent.call(context, {
    type: "speaker.final",
    speaker_id: "speaker_unknown",
    segment_id: "seg-2",
    text: "来，我们来告一下嘛，看能不能看清楚哈。那个到底四川话识不识别得清楚嘛？",
    start_ms: 0,
    end_ms: 13000,
  });
  page.handleSpeakerEvent.call(context, {
    type: "speaker.final",
    speaker_id: "speaker_unknown",
    segment_id: "seg-3",
    text: "来，我们来告一下嘛，看能不能看清楚哈。那个到底四川话识不识别得清楚嘛？四川话得不得行嘛？",
    start_ms: 0,
    end_ms: 24000,
  });

  assert.deepEqual(context.data.conversationLines.map((line) => line.text), [
    "四川话得不得行嘛？",
    "那个到底四川话识不识别得清楚嘛？",
    "来，我们来告一下嘛，看能不能看清楚哈。",
  ]);
  assert.deepEqual(context.data.conversationLines.map((line) => line.textClass), [
    "line-text latest",
    "line-text history",
    "line-text history",
  ]);
  assert.deepEqual(context.data.conversationLines.map((line) => line.metaText), [
    "0:13-0:24",
    "0:06-0:13",
    "0:00-0:06",
  ]);
});

test("speaker transcription page keeps only new final text when cumulative provider results omit timestamps", () => {
  const { page } = loadSpeakerTranscriptionPage();
  const context = pageContext(page);

  page.handleSpeakerEvent.call(context, {
    type: "speaker.final",
    speaker_id: "speaker_unknown",
    segment_id: "seg-1",
    text: "来，我们来告一下嘛，看能不能看清楚哈。",
  });
  page.handleSpeakerEvent.call(context, {
    type: "speaker.final",
    speaker_id: "speaker_unknown",
    segment_id: "seg-2",
    text: "来，我们来告一下嘛，看能不能看清楚哈。那个到底四川话识不识别得清楚嘛？",
  });
  page.handleSpeakerEvent.call(context, {
    type: "speaker.final",
    speaker_id: "speaker_unknown",
    segment_id: "seg-3",
    text: "来，我们来告一下嘛，看能不能看清楚哈。那个到底四川话识不识别得清楚嘛？",
  });

  assert.deepEqual(context.data.conversationLines.map((line) => line.text), [
    "那个到底四川话识不识别得清楚嘛？",
    "来，我们来告一下嘛，看能不能看清楚哈。",
  ]);
});

test("speaker transcription page ignores repeated final segment events", () => {
  const { page } = loadSpeakerTranscriptionPage({
    proofreadExports: {
      proofreadSpeakerTranscriptLine: () => new Promise(() => {}),
    },
  });
  const context = pageContext(page);
  let setDataCalls = 0;
  const originalSetData = context.setData;
  context.setData = function setDataWithCount(patch) {
    setDataCalls += 1;
    return originalSetData.call(this, patch);
  };

  const event = {
    type: "speaker.final",
    speaker_id: "speaker_unknown",
    segment_id: "seg-1",
    text: "这句话只应该出现一次。",
    start_ms: 0,
    end_ms: 3000,
  };

  page.handleSpeakerEvent.call(context, event);
  const callsAfterFirstFinal = setDataCalls;
  page.handleSpeakerEvent.call(context, event);

  assert.deepEqual(context.data.conversationLines.map((line) => line.text), ["这句话只应该出现一次。"]);
  assert.equal(setDataCalls, callsAfterFirstFinal);
});

test("speaker transcription page proofreads final lines with recent context", async () => {
  const calls = [];
  const { page } = loadSpeakerTranscriptionPage({
    proofreadExports: {
      proofreadSpeakerTranscriptLine: async (text, contextLines = []) => {
        calls.push({ text, contextLines });
        return {
          text,
          proofread_text: text.includes("得不得行") ? "四川话得不得行吗？" : "我们来告一下，看能不能看清楚。",
          changed: true,
          provider: "fake",
          context_line_count: contextLines.length,
        };
      },
    },
  });
  const context = pageContext(page);

  page.handleSpeakerEvent.call(context, {
    type: "speaker.final",
    speaker_id: "speaker_1",
    segment_id: "seg-1",
    text: "来，我们来告一下嘛，看能不能看清楚哈。",
    start_ms: 0,
    end_ms: 6000,
  });
  await flushPromises();

  page.handleSpeakerEvent.call(context, {
    type: "speaker.final",
    speaker_id: "speaker_2",
    segment_id: "seg-2",
    text: "四川话得不得行嘛？",
    start_ms: 7000,
    end_ms: 10000,
  });
  await flushPromises();

  assert.equal(calls.length, 2);
  assert.equal(calls[0].contextLines.length, 0);
  assert.deepEqual(calls[1].contextLines, [
    {
      speakerId: "speaker_1",
      text: "我们来告一下，看能不能看清楚。",
      startMs: 0,
      endMs: 6000,
    },
  ]);
  assert.equal(context.data.conversationLines[0].text, "四川话得不得行吗？");
  assert.equal(context.data.conversationLines[0].proofreadLoading, false);
  assert.equal(context.data.conversationLines[0].proofreadStatusText, "已校对");
});

test("speaker transcription page ignores stale proofreading for a revised segment", async () => {
  const pending = [];
  const { page } = loadSpeakerTranscriptionPage({
    proofreadExports: {
      proofreadSpeakerTranscriptLine: (text) => new Promise((resolve) => pending.push({ text, resolve })),
    },
  });
  const context = pageContext(page);

  page.handleSpeakerEvent.call(context, {
    type: "speaker.final",
    speaker_id: "speaker_1",
    segment_id: "seg-1",
    text: "第一版识别。",
    start_ms: 0,
    end_ms: 1000,
  });
  page.handleSpeakerEvent.call(context, {
    type: "speaker.final",
    speaker_id: "speaker_1",
    segment_id: "seg-1",
    text: "第二版识别。",
    start_ms: 0,
    end_ms: 1200,
  });

  pending[1].resolve({ proofread_text: "第二版校对。" });
  await flushPromises();
  pending[0].resolve({ proofread_text: "第一版校对。" });
  await flushPromises();

  assert.equal(context.data.conversationLines[0].text, "第二版校对。");
});

test("speaker transcription page preserves repeated text when provider reports a new start time", () => {
  const { page } = loadSpeakerTranscriptionPage();
  const context = pageContext(page);

  page.handleSpeakerEvent.call(context, {
    type: "speaker.final",
    speaker_id: "speaker_unknown",
    segment_id: "seg-1",
    text: "好。",
    start_ms: 0,
    end_ms: 1000,
  });
  page.handleSpeakerEvent.call(context, {
    type: "speaker.final",
    speaker_id: "speaker_unknown",
    segment_id: "seg-2",
    text: "好。",
    start_ms: 1000,
    end_ms: 2200,
  });

  assert.deepEqual(context.data.conversationLines.map((line) => line.text), ["好。", "好。"]);
  assert.deepEqual(context.data.conversationLines.map((line) => line.metaText), ["0:01-0:02", "0:00-0:01"]);
});

test("speaker transcription page keeps only new text from cumulative partial results", () => {
  const { page } = loadSpeakerTranscriptionPage();
  const context = pageContext(page);

  page.handleSpeakerEvent.call(context, {
    type: "speaker.final",
    speaker_id: "speaker_unknown",
    segment_id: "seg-1",
    text: "来，我们来告一下嘛，看能不能看清楚哈。",
    start_ms: 0,
    end_ms: 6000,
  });
  page.handleSpeakerEvent.call(context, {
    type: "speaker.partial",
    speaker_id: "speaker_unknown",
    text: "来，我们来告一下嘛，看能不能看清楚哈。那个到底四川话识不识别得清楚嘛？",
    start_ms: 0,
    end_ms: 13000,
  });

  assert.equal(context.data.activePartial.text, "那个到底四川话识不识别得清楚嘛？");
  assert.equal(context.data.activePartial.speakerTitle, "未分辨说话人");
  assert.equal(context.data.conversationLines.length, 1);
});

test("speaker transcription page keeps only new partial text when cumulative provider results omit timestamps", () => {
  const { page } = loadSpeakerTranscriptionPage();
  const context = pageContext(page);

  page.handleSpeakerEvent.call(context, {
    type: "speaker.final",
    speaker_id: "speaker_unknown",
    segment_id: "seg-1",
    text: "来，我们来告一下嘛，看能不能看清楚哈。",
  });
  page.handleSpeakerEvent.call(context, {
    type: "speaker.partial",
    speaker_id: "speaker_unknown",
    text: "来，我们来告一下嘛，看能不能看清楚哈。那个到底四川话识不识别得清楚嘛？",
  });

  assert.equal(context.data.activePartial.text, "那个到底四川话识不识别得清楚嘛？");
  assert.equal(context.data.conversationLines.length, 1);
});

test("speaker transcription page scrolls long partial text to the newest progress", () => {
  const { page } = loadSpeakerTranscriptionPage();
  const context = pageContext(page);
  const longPartial = "这是一段很长的实时识别文字，".repeat(8);

  page.handleSpeakerEvent.call(context, {
    type: "speaker.partial",
    speaker_id: "speaker_2",
    text: longPartial,
  });
  assert.equal(context.data.activePartial.text, longPartial);
  assert.equal(context.data.activePartial.partialScrollTop, 10000);

  page.handleSpeakerEvent.call(context, {
    type: "speaker.partial",
    speaker_id: "speaker_2",
    text: `${longPartial}继续补充到最新进度。`,
  });

  assert.equal(context.data.activePartial.partialScrollTop, 20000);
});

test("speaker transcription page ignores repeated partial text without moving scroll", () => {
  const { page } = loadSpeakerTranscriptionPage();
  const context = pageContext(page);
  const longPartial = "这是一段很长的实时识别文字，".repeat(8);
  let setDataCalls = 0;
  const originalSetData = context.setData;
  context.setData = function setDataWithCount(patch) {
    setDataCalls += 1;
    return originalSetData.call(this, patch);
  };

  const event = {
    type: "speaker.partial",
    speaker_id: "speaker_2",
    segment_id: "seg-2",
    text: longPartial,
  };

  page.handleSpeakerEvent.call(context, event);
  const callsAfterFirstPartial = setDataCalls;
  const scrollTopAfterFirstPartial = context.data.activePartial.partialScrollTop;
  page.handleSpeakerEvent.call(context, event);

  assert.equal(context.data.activePartial.text, longPartial);
  assert.equal(context.data.activePartial.partialScrollTop, scrollTopAfterFirstPartial);
  assert.equal(setDataCalls, callsAfterFirstPartial);
});

test("speaker transcription page follows the shared large text setting", () => {
  const { page } = loadSpeakerTranscriptionPage({
    accessibilityExports: {
      readAccessibilitySettings: () => ({
        largeTextEnabled: true,
        autoBargeInEnabled: true,
      }),
    },
  });
  const context = pageContext(page);

  page.onLoad.call(context);

  assert.equal(context.data.pageClass, "speaker-transcription-page large-text");
});

test("speaker transcription page clears transcript only while idle", () => {
  const { page } = loadSpeakerTranscriptionPage();
  const context = pageContext(page);
  page.handleSpeakerEvent.call(context, {
    type: "speaker.final",
    speaker_id: "speaker_unknown",
    segment_id: "seg-1",
    text: "现在先试一下。",
  });

  assert.equal(context.data.conversationLines.length, 1);
  page.handleDiarizationStatus.call(context, {
    type: "speaker.diarization",
    status: "missing_provider_labels",
  });
  page.clearTranscript.call(context);

  assert.deepEqual(context.data.conversationLines, []);
  assert.equal(context.data.activePartial.visible, false);
  assert.equal(context.data.showEmpty, true);
  assert.equal(context.data.controlPanelClass, "control-panel");
  assert.equal(context.data.showFullControls, true);
  assert.equal(context.data.showDiarizationNotice, false);
  assert.equal(context.data.latestFinalLineId, "");
});
