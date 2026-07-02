require("./register-ts");

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const { loadPageModule, mockModule, pageContext } = require("./page-harness");

function sampleTranscript(id, overrides = {}) {
  return {
    id,
    voice_session_id: "voice-1",
    project_id: "project-1",
    project_title: "我的回忆录",
    chat_message_id: `message-${id}`,
    audio_file_id: `audio-${id}`,
    audio_mime_type: "audio/pcm",
    audio_size_bytes: 1024,
    audio_duration_seconds: 32,
    transcript_version_id: `version-${id}`,
    question_message_id: `question-${id}`,
    question_text: "那时候家里谁照顾你？",
    question_audio_file_id: `question-audio-${id}`,
    transcript_raw: "爷爷退休后还在帮公司做事。",
    transcript_edited: "",
    display_text: "爷爷退休后还在帮公司做事。",
    summary_title: "爷爷退休后仍帮公司做事",
    summary_text: "讲述者提到爷爷退休后仍继续帮公司处理事务。",
    summary_source: "source_card",
    raw_char_count: 14,
    edited_char_count: 0,
    display_char_count: 14,
    start_ms: 0,
    end_ms: 32000,
    provider: "fake",
    session_status: "stopped",
    guidance_mode: "free_narration",
    current_guidance_module: null,
    is_included_in_book: false,
    included_chapters: [],
    created_at: "2026-07-01T07:38:00.000Z",
    session_created_at: "2026-07-01T07:00:00.000Z",
    ...overrides,
  };
}

function sampleSourceRef(id, quote = "爷爷退休后还在帮公司做事。") {
  return {
    voice_session_id: "voice-1",
    message_id: `message-${id}`,
    utterance_id: id,
    transcript_version_id: `version-${id}`,
    audio_file_id: `audio-${id}`,
    start_ms: 0,
    end_ms: 32000,
    quote,
  };
}

function sampleBookPlan() {
  return {
    id: "plan-1",
    project_id: "project-1",
    title: "章节规划",
    status: "confirmed",
    created_by: "ai",
    model: "fake",
    metadata_json: {},
    created_at: "2026-07-01T00:00:00.000Z",
    updated_at: "2026-07-01T00:00:00.000Z",
    chapters: [
      {
        id: "chapter-1",
        plan_id: "plan-1",
        project_id: "project-1",
        title: "退休后的日子",
        sort_order: 0,
        source_refs: [sampleSourceRef("utterance-included"), sampleSourceRef("utterance-other")],
        metadata_json: {},
        created_at: "2026-07-01T00:00:00.000Z",
        updated_at: "2026-07-01T00:00:00.000Z",
      },
      {
        id: "chapter-2",
        plan_id: "plan-1",
        project_id: "project-1",
        title: "家庭记忆",
        sort_order: 1,
        source_refs: [],
        metadata_json: {},
        created_at: "2026-07-01T00:00:00.000Z",
        updated_at: "2026-07-01T00:00:00.000Z",
      },
    ],
  };
}

function loadTranscriptDetailPage({ voiceApiExports = {}, voiceSearchExports = {} } = {}) {
  mockModule("../services/memoir-voice-api.ts", {
    assignMemoirBookSources: async () => {
      throw new Error("assignMemoirBookSources was not mocked");
    },
    deleteMemoirVoiceTranscript: async () => {
      throw new Error("deleteMemoirVoiceTranscript was not mocked");
    },
    getMemoirBookPlan: async () => null,
    getMemoirVoiceTranscript: async () => {
      throw new Error("getMemoirVoiceTranscript was not mocked");
    },
    moveMemoirBookSource: async () => {
      throw new Error("moveMemoirBookSource was not mocked");
    },
    previewMemoirVoiceTranscriptVoiceEdit: async () => {
      throw new Error("previewMemoirVoiceTranscriptVoiceEdit was not mocked");
    },
    updateMemoirVoiceTranscript: async () => {
      throw new Error("updateMemoirVoiceTranscript was not mocked");
    },
    ...voiceApiExports,
  });
  mockModule("../services/memoir-voice-search.ts", {
    recordMemoirVoiceSearchQuery: async () => {
      throw new Error("recordMemoirVoiceSearchQuery was not mocked");
    },
    stopMemoirVoiceSearchRecording: () => {},
    ...voiceSearchExports,
  });
  return loadPageModule("../pages/transcript-detail/index.ts");
}

test("transcript detail page loads a single transcript and chapter options", async () => {
  const calls = [];
  const { page } = loadTranscriptDetailPage({
    voiceApiExports: {
      getMemoirVoiceTranscript: async (id) => {
        calls.push(id);
        return sampleTranscript(id);
      },
      getMemoirBookPlan: async () => sampleBookPlan(),
    },
  });
  const context = pageContext(page);

  await page.onLoad.call(context, { id: "utterance-new" });

  assert.deepEqual(calls, ["utterance-new"]);
  assert.equal(context.data.item.titleText, "爷爷退休后仍帮公司做事");
  assert.equal(context.data.item.createdAtText, "07-01 15:38");
  assert.equal(context.data.item.audioText, "32 秒");
  assert.equal(context.data.item.recordMetaText, "07-01 15:38 · 32 秒");
  assert.equal(context.data.item.bookInclusionText, "尚未入章");
  assert.deepEqual(context.data.item.chapterAssignOptions, [
    { label: "第 1 章 退休后的日子", value: "chapter-1" },
    { label: "第 2 章 家庭记忆", value: "chapter-2" },
  ]);
});

test("transcript detail page uses content-first inclusion labels", async () => {
  const { page } = loadTranscriptDetailPage({
    voiceApiExports: {
      getMemoirVoiceTranscript: async (id) => sampleTranscript(id, {
        is_included_in_book: true,
        included_chapters: [
          { chapter_id: "chapter-2", title: "家庭记忆", sort_order: 1 },
          { chapter_id: "chapter-1", title: "退休后的日子", sort_order: 0 },
        ],
      }),
      getMemoirBookPlan: async () => sampleBookPlan(),
    },
  });
  const context = pageContext(page);

  await page.onLoad.call(context, { id: "utterance-included" });

  assert.equal(context.data.item.bookInclusionText, "已放入第 1 章：退休后的日子等 2 章");
  assert.equal(context.data.item.isIncludedInBook, true);
  assert.deepEqual(context.data.item.chapterMoveOptions, [
    { label: "第 2 章 家庭记忆", value: "chapter-2" },
  ]);
});

test("transcript detail page keeps serious evidence and editing labels", () => {
  const wxml = fs.readFileSync(
    path.join(__dirname, "../pages/transcript-detail/index.wxml"),
    "utf8",
  );

  assert.match(wxml, /整理后文字/);
  assert.match(wxml, /用于生成章节正文/);
  assert.match(wxml, /原始识别/);
  assert.match(wxml, /保留口述，不建议直接改成书稿/);
  assert.match(wxml, /校对整理文字/);
  assert.match(wxml, /说出修改/);
  assert.match(wxml, /危险操作/);
  assert.doesNotMatch(wxml, /语音修正|语音校对|播放问答/);
});

test("transcript detail page can open directly in edit mode", async () => {
  const { page } = loadTranscriptDetailPage({
    voiceApiExports: {
      getMemoirVoiceTranscript: async (id) => sampleTranscript(id),
    },
  });
  const context = pageContext(page);

  await page.onLoad.call(context, { id: "utterance-edit", mode: "edit" });

  assert.equal(context.data.item.isEditing, true);
  assert.equal(context.data.item.editingText, "爷爷退休后还在帮公司做事。");
});

test("transcript detail page saves edited display text", async () => {
  const updates = [];
  const toasts = [];
  global.wx = {
    showToast(options) {
      toasts.push(options);
    },
  };
  const { page } = loadTranscriptDetailPage({
    voiceApiExports: {
      getMemoirVoiceTranscript: async (id) => sampleTranscript(id),
      updateMemoirVoiceTranscript: async (id, content) => {
        updates.push({ id, content });
        return sampleTranscript(id, {
          transcript_edited: content,
          display_text: content,
          display_char_count: content.length,
          summary_title: "爷爷退休后帮公司处理事务",
        });
      },
    },
  });
  const context = pageContext(page, { utteranceId: "utterance-edit" });

  await page.loadTranscript.call(context);
  page.beginEdit.call(context);
  page.changeText.call(context, { detail: { value: "爷爷退休后还帮公司处理事情。" } });
  await page.saveTranscript.call(context);

  assert.deepEqual(updates, [
    { id: "utterance-edit", content: "爷爷退休后还帮公司处理事情。" },
  ]);
  assert.equal(context.data.item.display_text, "爷爷退休后还帮公司处理事情。");
  assert.equal(context.data.item.isEditing, false);
  assert.deepEqual(toasts, [{ title: "已保存", icon: "success" }]);
  delete global.wx;
});

test("transcript detail page deletes after confirmation and navigates back", async () => {
  const deleted = [];
  const toasts = [];
  const navigations = [];
  global.wx = {
    showModal(options) {
      options.success({ confirm: true });
    },
    showToast(options) {
      toasts.push(options);
    },
    navigateBack(options) {
      navigations.push(options);
    },
  };
  const { page } = loadTranscriptDetailPage({
    voiceApiExports: {
      getMemoirVoiceTranscript: async (id) => sampleTranscript(id),
      deleteMemoirVoiceTranscript: async (id) => {
        deleted.push(id);
      },
    },
  });
  const context = pageContext(page, { utteranceId: "utterance-delete" });

  await page.loadTranscript.call(context);
  await page.deleteTranscript.call(context);

  assert.deepEqual(deleted, ["utterance-delete"]);
  assert.deepEqual(toasts, [{ title: "已删除", icon: "success" }]);
  assert.deepEqual(navigations, [{ delta: 1 }]);
  delete global.wx;
});
