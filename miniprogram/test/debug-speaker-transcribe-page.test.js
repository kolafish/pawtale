require("./register-ts");

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const { loadPageModule, pageContext } = require("./page-harness");

function loadDebugSpeakerPage() {
  delete require.cache[require.resolve("../pages/debug-speaker-transcribe/index.ts")];
  return loadPageModule("../pages/debug-speaker-transcribe/index.ts");
}

test("debug speaker page explains that realtime transcript is not saved", () => {
  const wxml = fs.readFileSync(
    path.join(__dirname, "../pages/debug-speaker-transcribe/index.wxml"),
    "utf8",
  );
  const wxss = fs.readFileSync(
    path.join(__dirname, "../pages/debug-speaker-transcribe/index.wxss"),
    "utf8",
  );

  assert.match(wxml, /本次识别不保存到素材库/);
  assert.match(wxml, /scroll-into-view/);
  assert.match(wxml, /partialScrollTarget/);
  assert.match(wxml, /speaker-list/);
  assert.match(wxml, /audioLevelStyle/);
  assert.match(wxss, /font-variant-numeric: tabular-nums/);
  assert.match(wxss, /\.partial-scroll\.long[\s\S]*height: 420rpx/);
  assert.match(wxss, /\.line-text[\s\S]*font-size: 44rpx/);
  assert.doesNotMatch(wxml, /章节|成书文字|素材库移除/);
});

test("debug speaker page groups partial and final text by speaker windows", () => {
  const { page } = loadDebugSpeakerPage();
  const context = pageContext(page);

  page.handleSpeakerEvent.call(context, {
    type: "speaker.partial",
    speaker_id: "speaker_1",
    text: "你小时候住在哪里？",
    start_ms: 0,
    end_ms: 1200,
  });
  page.handleSpeakerEvent.call(context, {
    type: "speaker.final",
    speaker_id: "speaker_2",
    segment_id: "seg-2",
    text: "住在厂里的家属院。",
    start_ms: 1500,
    end_ms: 3600,
    confidence: 0.86,
  });

  assert.equal(context.data.speakers.length, 2);
  assert.equal(context.data.speakers[0].title, "说话人 1");
  assert.equal(context.data.speakers[0].partialText, "你小时候住在哪里？");
  assert.equal(context.data.speakers[0].cardClass, "speaker-window active");
  assert.equal(context.data.speakers[1].title, "说话人 2");
  assert.equal(context.data.speakers[1].lines[0].text, "住在厂里的家属院。");
  assert.equal(context.data.speakers[1].lines[0].metaText, "0:01-0:03 · 置信度 86%");
  assert.equal(context.data.showEmpty, false);
  assert.equal(context.data.clearDisabled, false);
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
  const firstSpeaker = context.data.speakers[0];

  assert.equal(firstSpeaker.partialText, longPartial);
  assert.equal(firstSpeaker.partialScrollClass, "partial-scroll long");
  assert.equal(firstSpeaker.partialScrollTop, 10000);
  assert.equal(firstSpeaker.partialScrollTarget, firstSpeaker.partialEndId);

  page.handleSpeakerEvent.call(context, {
    type: "speaker.partial",
    speaker_id: "speaker_2",
    text: `${longPartial}继续补充到最新进度。`,
  });

  assert.equal(context.data.speakers[0].partialScrollTop, 20000);
  assert.match(context.data.speakers[0].partialScrollTarget, /speaker_2-partial-end-20000/);
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
  page.clearTranscript.call(context);

  assert.deepEqual(context.data.speakers, []);
  assert.equal(context.data.showEmpty, true);
  assert.equal(context.data.elapsedText, "0:00");
});

test("debug speaker page formats compact stats", () => {
  const { mod } = loadDebugSpeakerPage();

  assert.equal(mod.formatDebugSpeakerElapsed(0), "0:00");
  assert.equal(mod.formatDebugSpeakerElapsed(65_000), "1:05");
  assert.equal(mod.formatDebugSpeakerBytes(512), "512 B");
  assert.equal(mod.formatDebugSpeakerBytes(2048), "2 KB");
});
