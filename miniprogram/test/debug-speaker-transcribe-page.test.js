require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");
const { loadPageModule, mockModule, pageContext } = require("./page-harness");

function loadDebugSpeakerPage({ accessibilityExports = {}, proofreadExports = {} } = {}) {
  mockModule("../services/accessibility-settings.ts", {
    readAccessibilitySettings: () => ({
      largeTextEnabled: false,
      autoBargeInEnabled: true,
    }),
    ...accessibilityExports,
  });
  mockModule("../services/debug-speaker-proofread.ts", {
    proofreadDebugSpeakerTranscriptLine: async (text, contextLines = []) => ({
      text,
      proofread_text: text,
      changed: false,
      provider: "test",
      context_line_count: contextLines.length,
    }),
    ...proofreadExports,
  });
  delete require.cache[require.resolve("../pages/debug-speaker-transcribe/index.ts")];
  return loadPageModule("../pages/debug-speaker-transcribe/index.ts");
}

function flushPromises() {
  return new Promise((resolve) => setImmediate(resolve));
}

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function lineTouchEvent(speakerId, lineId, previewEnabled = true, clientX = 24, clientY = 36) {
  return {
    currentTarget: {
      dataset: {
        speakerId,
        lineId,
        previewEnabled,
      },
    },
    touches: [{ clientX, clientY }],
    changedTouches: [{ clientX, clientY }],
  };
}

test("debug speaker page shows partial and final text in conversation flow", () => {
  const { page } = loadDebugSpeakerPage();
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
  assert.equal(context.data.conversationScrollTarget, "");
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

  assert.equal(context.data.speakers.length, 2);
  assert.equal(context.data.speakers[0].title, "说话人 1");
  assert.equal(context.data.speakers[1].title, "女1");
  assert.equal(context.data.activePartial.visible, false);
  assert.equal(context.data.conversationLines.length, 1);
  assert.equal(context.data.conversationLines[0].speakerTitle, "女1");
  assert.equal(context.data.conversationLines[0].speakerBadgeClass, "speaker-badge female");
  assert.equal(context.data.conversationLines[0].text, "住在厂里的家属院。");
  assert.equal(context.data.conversationLines[0].metaText, "0:01-0:03 · 置信度 86%");
  assert.equal(context.data.conversationLines[0].lineClass, "line-item latest");
  assert.equal(context.data.conversationLines[0].textClass, "line-text latest");
  assert.equal(context.data.conversationScrollTarget, "");
  assert.equal(context.data.showEmpty, false);
  assert.equal(context.data.clearDisabled, false);
});

test("debug speaker page shows live tail above finalized utterance", () => {
  const { page } = loadDebugSpeakerPage();
  const context = pageContext(page);

  page.handleSpeakerEvent.call(context, {
    type: "speaker.final",
    speaker_id: "speaker_1",
    segment_id: "seg-1",
    text: "第一句已经稳定。",
    start_ms: 0,
    end_ms: 1800,
  });
  page.handleSpeakerEvent.call(context, {
    type: "speaker.partial",
    speaker_id: "speaker_unknown",
    segment_id: "seg-tail",
    text: "第二句还在实时说",
    start_ms: 1800,
    provider_event_type: "final",
  });

  assert.equal(context.data.conversationLines.length, 1);
  assert.equal(context.data.conversationLines[0].text, "第一句已经稳定。");
  assert.equal(context.data.activePartial.visible, true);
  assert.equal(context.data.activePartial.speakerTitle, "未分辨说话人");
  assert.equal(context.data.activePartial.text, "第二句还在实时说");
  assert.equal(context.data.showEmpty, false);
});

test("debug speaker page keeps live unknown partial when an earlier speaker final arrives", () => {
  const { page } = loadDebugSpeakerPage();
  const context = pageContext(page);

  page.handleSpeakerEvent.call(context, {
    type: "speaker.partial",
    speaker_id: "speaker_unknown",
    segment_id: "live-current",
    text: "第一句已经稳定。第二句还在实时说",
    provider_event_type: "live.partial",
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
  assert.equal(context.data.activePartial.text, "第一句已经稳定。第二句还在实时说");
});

test("debug speaker page clears live unknown partial when final text covers it", () => {
  const { page } = loadDebugSpeakerPage();
  const context = pageContext(page);

  page.handleSpeakerEvent.call(context, {
    type: "speaker.partial",
    speaker_id: "speaker_unknown",
    segment_id: "live-current",
    text: "第二句已经稳定。",
    provider_event_type: "live.partial",
  });
  page.handleSpeakerEvent.call(context, {
    type: "speaker.final",
    speaker_id: "speaker_2",
    segment_id: "seg-2",
    text: "第二句已经稳定。",
  });

  assert.equal(context.data.activePartial.visible, false);
});

test("debug speaker page keeps transcript area high after recording stops", () => {
  const { page } = loadDebugSpeakerPage();
  const context = pageContext(page);

  page.handleSpeakerEvent.call(context, {
    type: "speaker.final",
    speaker_id: "speaker_1",
    segment_id: "seg-1",
    text: "这句识别出来以后，控制区应该收起来。",
  });
  page.setStatus.call(context, "closed");

  assert.equal(context.data.controlPanelClass, "control-panel compact");
  assert.equal(context.data.showFullControls, false);
  assert.equal(context.data.showEmpty, false);
});

test("debug speaker page keeps final text newest-first with gender labels", async () => {
  const { page } = loadDebugSpeakerPage();
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

test("debug speaker page shows provider diarization diagnostics", () => {
  const { page } = loadDebugSpeakerPage();
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

test("debug speaker page keeps only the newest final line visually large", async () => {
  const pendingProofreads = [];
  const { page } = loadDebugSpeakerPage({
    proofreadExports: {
      proofreadDebugSpeakerTranscriptLine: (text) => new Promise((resolve) => {
        pendingProofreads.push({ text, resolve });
      }),
    },
  });
  const context = pageContext(page);

  page.handleSpeakerEvent.call(context, {
    type: "speaker.final",
    speaker_id: "speaker_1",
    segment_id: "seg-1",
    text: "第一位说话人先说了一句。",
    start_ms: 0,
    end_ms: 2400,
  });
  const firstLineId = context.data.conversationLines[0].id;

  page.handleSpeakerEvent.call(context, {
    type: "speaker.final",
    speaker_id: "speaker_2",
    segment_id: "seg-2",
    text: "第二位说话人接着补充。",
    start_ms: 2500,
    end_ms: 5200,
  });
  const secondLineId = context.data.conversationLines[0].id;

  assert.equal(context.data.latestFinalLineId, secondLineId);
  assert.equal(context.data.conversationLines[0].id, secondLineId);
  assert.equal(context.data.conversationLines[0].lineClass, "line-item latest");
  assert.equal(context.data.conversationLines[0].textClass, "line-text latest");
  assert.equal(context.data.conversationLines[1].id, firstLineId);
  assert.equal(context.data.conversationLines[1].lineClass, "line-item history");
  assert.equal(context.data.conversationLines[1].textClass, "line-text history");

  pendingProofreads[1].resolve({
    proofread_text: "第二位说话人接着补充。",
  });
  await flushPromises();
  pendingProofreads[0].resolve({
    proofread_text: "第一位说话人先说了一句。",
  });
  await flushPromises();

  assert.equal(context.data.latestFinalLineId, secondLineId);
  assert.equal(context.data.conversationLines[0].lineClass, "line-item latest");
  assert.equal(context.data.conversationLines[1].lineClass, "line-item history");
});

test("debug speaker page previews historical final text while pressed", async () => {
  const { page } = loadDebugSpeakerPage();
  const context = pageContext(page);

  page.handleSpeakerEvent.call(context, {
    type: "speaker.final",
    speaker_id: "speaker_1",
    segment_id: "seg-1",
    text: "第一句会变成历史文字。",
    start_ms: 0,
    end_ms: 1600,
  });
  page.handleSpeakerEvent.call(context, {
    type: "speaker.final",
    speaker_id: "speaker_1",
    segment_id: "seg-2",
    text: "第二句是最新文字。",
    start_ms: 1700,
    end_ms: 3400,
  });
  await flushPromises();

  const latestLine = context.data.conversationLines[0];
  const historyLine = context.data.conversationLines[1];

  assert.equal(latestLine.previewEnabled, false);
  assert.equal(historyLine.previewEnabled, true);

  page.handleLineTouchStart.call(
    context,
    lineTouchEvent(historyLine.speakerId, historyLine.id, historyLine.previewEnabled),
  );
  assert.equal(context.data.historyLinePreview.visible, false);

  await wait(180);

  assert.equal(context.data.historyLinePreview.visible, true);
  assert.equal(context.data.historyLinePreview.speakerTitle, "说话人 1");
  assert.equal(context.data.historyLinePreview.metaText, "0:00-0:01");
  assert.equal(context.data.historyLinePreview.statusText, "已校对");
  assert.equal(context.data.historyLinePreview.text, "第一句会变成历史文字。");

  page.handleLineTouchEnd.call(context);

  assert.equal(context.data.historyLinePreview.visible, false);
});

test("debug speaker page does not preview latest final text or scrolling touches", async () => {
  const { page } = loadDebugSpeakerPage();
  const context = pageContext(page);

  page.handleSpeakerEvent.call(context, {
    type: "speaker.final",
    speaker_id: "speaker_1",
    segment_id: "seg-1",
    text: "先说的历史内容。",
  });
  page.handleSpeakerEvent.call(context, {
    type: "speaker.final",
    speaker_id: "speaker_1",
    segment_id: "seg-2",
    text: "当前最新内容。",
  });

  const latestLine = context.data.conversationLines[0];
  const historyLine = context.data.conversationLines[1];

  page.handleLineTouchStart.call(
    context,
    lineTouchEvent(latestLine.speakerId, latestLine.id, latestLine.previewEnabled),
  );
  await wait(180);
  assert.equal(context.data.historyLinePreview.visible, false);

  page.handleLineTouchStart.call(
    context,
    lineTouchEvent(historyLine.speakerId, historyLine.id, historyLine.previewEnabled, 30, 40),
  );
  page.handleLineTouchMove.call(
    context,
    lineTouchEvent(historyLine.speakerId, historyLine.id, historyLine.previewEnabled, 30, 70),
  );
  await wait(180);

  assert.equal(context.data.historyLinePreview.visible, false);
});

test("debug speaker page keeps only new text from cumulative final results", () => {
  const { page } = loadDebugSpeakerPage();
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

  assert.equal(context.data.speakers.length, 1);
  assert.equal(context.data.speakers[0].segmentCountText, "3 段");
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

test("debug speaker page keeps only new final text when cumulative provider results omit timestamps", () => {
  const { page } = loadDebugSpeakerPage();
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

  assert.equal(context.data.speakers.length, 1);
  assert.equal(context.data.speakers[0].segmentCountText, "2 段");
  assert.deepEqual(context.data.conversationLines.map((line) => line.text), [
    "那个到底四川话识不识别得清楚嘛？",
    "来，我们来告一下嘛，看能不能看清楚哈。",
  ]);
});

test("debug speaker page ignores repeated final segment events", () => {
  const { page } = loadDebugSpeakerPage({
    proofreadExports: {
      proofreadDebugSpeakerTranscriptLine: () => new Promise(() => {}),
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

  assert.equal(context.data.speakers[0].segmentCountText, "1 段");
  assert.deepEqual(context.data.conversationLines.map((line) => line.text), ["这句话只应该出现一次。"]);
  assert.equal(setDataCalls, callsAfterFirstFinal);
});

test("debug speaker page proofreads final lines with recent context", async () => {
  const calls = [];
  const { page } = loadDebugSpeakerPage({
    proofreadExports: {
      proofreadDebugSpeakerTranscriptLine: async (text, contextLines = []) => {
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

test("debug speaker page preserves repeated text when provider reports a new start time", () => {
  const { page } = loadDebugSpeakerPage();
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

  assert.equal(context.data.speakers[0].segmentCountText, "2 段");
  assert.deepEqual(context.data.conversationLines.map((line) => line.text), ["好。", "好。"]);
  assert.deepEqual(context.data.conversationLines.map((line) => line.metaText), ["0:01-0:02", "0:00-0:01"]);
});

test("debug speaker page keeps only new text from cumulative partial results", () => {
  const { page } = loadDebugSpeakerPage();
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
  assert.equal(context.data.speakers[0].lines.length, 1);
});

test("debug speaker page keeps only new partial text when cumulative provider results omit timestamps", () => {
  const { page } = loadDebugSpeakerPage();
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
  assert.equal(context.data.speakers[0].lines.length, 1);
});

test("debug speaker page scrolls long partial text to the newest progress", () => {
  const { page } = loadDebugSpeakerPage();
  const context = pageContext(page);
  const longPartial = "这是一段很长的实时识别文字，".repeat(8);

  page.handleSpeakerEvent.call(context, {
    type: "speaker.partial",
    speaker_id: "speaker_2",
    text: longPartial,
  });
  assert.equal(context.data.activePartial.text, longPartial);
  assert.equal(context.data.activePartial.partialScrollClass, "partial-scroll long");
  assert.equal(context.data.activePartial.partialScrollTop, 10000);
  assert.equal(context.data.activePartial.partialScrollTarget, context.data.activePartial.partialEndId);

  page.handleSpeakerEvent.call(context, {
    type: "speaker.partial",
    speaker_id: "speaker_2",
    text: `${longPartial}继续补充到最新进度。`,
  });

  assert.equal(context.data.activePartial.partialScrollTop, 20000);
  assert.match(context.data.activePartial.partialScrollTarget, /speaker_2-partial-end-20000/);
});

test("debug speaker page ignores repeated partial text without moving scroll", () => {
  const { page } = loadDebugSpeakerPage();
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

test("debug speaker page follows the shared large text setting", () => {
  const { mod, page } = loadDebugSpeakerPage({
    accessibilityExports: {
      readAccessibilitySettings: () => ({
        largeTextEnabled: true,
        autoBargeInEnabled: true,
      }),
    },
  });
  const context = pageContext(page);

  page.onLoad.call(context);

  assert.equal(context.data.pageClass, "debug-speaker-page large-text");
  assert.equal(mod.debugSpeakerPageClass(false), "debug-speaker-page");
  assert.equal(mod.debugSpeakerPageClass(true), "debug-speaker-page large-text");
});

test("debug speaker page throttles stats rendering while recording", () => {
  const { page } = loadDebugSpeakerPage();
  const context = pageContext(page);
  context.data.recording = true;

  assert.equal(page.shouldRenderStats.call(context, 1000), true);
  assert.equal(page.shouldRenderStats.call(context, 1200), false);
  assert.equal(page.shouldRenderStats.call(context, 1799), false);
  assert.equal(page.shouldRenderStats.call(context, 1800), true);

  context.data.recording = false;
  assert.equal(page.shouldRenderStats.call(context, 1801), true);
});

test("debug speaker page clears transcript only while idle", () => {
  const { page } = loadDebugSpeakerPage();
  const context = pageContext(page);
  page.handleSpeakerEvent.call(context, {
    type: "speaker.final",
    speaker_id: "speaker_unknown",
    segment_id: "seg-1",
    text: "现在先试一下。",
  });

  assert.equal(context.data.speakers[0].title, "未分辨说话人");
  assert.equal(context.data.conversationLines.length, 1);
  page.handleDiarizationStatus.call(context, {
    type: "speaker.diarization",
    status: "missing_provider_labels",
  });
  page.clearTranscript.call(context);

  assert.deepEqual(context.data.speakers, []);
  assert.deepEqual(context.data.conversationLines, []);
  assert.equal(context.data.activePartial.visible, false);
  assert.equal(context.data.showEmpty, true);
  assert.equal(context.data.controlPanelClass, "control-panel");
  assert.equal(context.data.showFullControls, true);
  assert.equal(context.data.showDiarizationNotice, false);
  assert.equal(context.data.latestFinalLineId, "");
  assert.equal(context.data.elapsedText, "0:00");
});

test("debug speaker page formats compact stats", () => {
  const { mod } = loadDebugSpeakerPage();

  assert.equal(mod.formatDebugSpeakerElapsed(0), "0:00");
  assert.equal(mod.formatDebugSpeakerElapsed(65_000), "1:05");
  assert.equal(mod.formatDebugSpeakerBytes(512), "512 B");
  assert.equal(mod.formatDebugSpeakerBytes(2048), "2 KB");
});
