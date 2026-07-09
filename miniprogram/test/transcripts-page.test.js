require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");
const { loadPageModule, mockModule, pageContext } = require("./page-harness");

function loadTranscriptsPage({ authExports = {}, voiceApiExports = {}, voiceSearchExports = {} } = {}) {
  mockModule("../services/auth.ts", {
    getMemoirIdentity: async () => {
      throw new Error("getMemoirIdentity was not mocked");
    },
    ...authExports,
  });
  mockModule("../services/memoir-voice-api.ts", {
    assignMemoirBookSources: async () => {
      throw new Error("assignMemoirBookSources was not mocked");
    },
    deleteMemoirVoiceTranscript: async () => {
      throw new Error("deleteMemoirVoiceTranscript was not mocked");
    },
    downloadMemoirVoiceAudio: async () => {
      throw new Error("downloadMemoirVoiceAudio was not mocked");
    },
    getMemoirBookPlan: async () => null,
    listMemoirVoiceTranscripts: async () => {
      throw new Error("listMemoirVoiceTranscripts was not mocked");
    },
    parseMemoirVoiceSearchQuery: async () => {
      throw new Error("parseMemoirVoiceSearchQuery was not mocked");
    },
    previewMemoirVoiceTranscriptVoiceEdit: async () => {
      throw new Error("previewMemoirVoiceTranscriptVoiceEdit was not mocked");
    },
    recoverMemoirVoiceTranscript: async () => {
      throw new Error("recoverMemoirVoiceTranscript was not mocked");
    },
    moveMemoirBookSource: async () => {
      throw new Error("moveMemoirBookSource was not mocked");
    },
    updateMemoirVoiceTranscript: async () => {
      throw new Error("updateMemoirVoiceTranscript was not mocked");
    },
    updateMemoirBookPlan: async () => {
      throw new Error("updateMemoirBookPlan was not mocked");
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

  return loadPageModule("../pages/transcripts/index.ts");
}

function sampleTranscript(id, createdAt = "2026-06-17T00:00:00.000Z") {
  return {
    id,
    voice_session_id: "voice-1",
    project_id: "project-1",
    project_title: "我的回忆录",
    chat_message_id: `message-${id}`,
    audio_file_id: `audio-${id}`,
    audio_mime_type: "audio/pcm",
    audio_size_bytes: 1024,
    audio_duration_seconds: 8,
    transcript_version_id: `version-${id}`,
    question_message_id: null,
    question_text: "",
    question_audio_file_id: null,
    transcript_raw: `原文 ${id}`,
    transcript_edited: "",
    display_text: `原文 ${id}`,
    raw_char_count: 5,
    edited_char_count: 0,
    display_char_count: 5,
    start_ms: 0,
    end_ms: 8000,
    provider: "fake",
    session_status: "stopped",
    guidance_mode: "free_narration",
    current_guidance_module: null,
    is_included_in_book: false,
    included_chapters: [],
    created_at: createdAt,
    session_created_at: "2026-06-17T00:00:00.000Z",
  };
}

function sampleSourceRef(id, quote = `原文 ${id}`) {
  return {
    voice_session_id: "voice-1",
    message_id: `message-${id}`,
    utterance_id: id,
    transcript_version_id: `version-${id}`,
    audio_file_id: `audio-${id}`,
    start_ms: 0,
    end_ms: 8000,
    quote,
  };
}

function sampleBookPlan() {
  return {
    id: "plan-1",
    project_id: "project-1",
    title: "我的回忆录章节规划",
    status: "confirmed",
    created_by: "ai",
    model: "fake",
    metadata_json: {},
    created_at: "2026-06-17T00:00:00.000Z",
    updated_at: "2026-06-17T00:00:00.000Z",
    chapters: [
      {
        id: "chapter-1",
        plan_id: "plan-1",
        project_id: "project-1",
        title: "岁首欢聚记",
        sort_order: 0,
        source_refs: [sampleSourceRef("utterance-1"), sampleSourceRef("utterance-2")],
        metadata_json: {},
        created_at: "2026-06-17T00:00:00.000Z",
        updated_at: "2026-06-17T00:00:00.000Z",
      },
      {
        id: "chapter-2",
        plan_id: "plan-1",
        project_id: "project-1",
        title: "童年友情",
        sort_order: 1,
        source_refs: [sampleSourceRef("utterance-3")],
        metadata_json: {},
        created_at: "2026-06-17T00:00:00.000Z",
        updated_at: "2026-06-17T00:00:00.000Z",
      },
    ],
  };
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((promiseResolve, promiseReject) => {
    resolve = promiseResolve;
    reject = promiseReject;
  });
  return { promise, resolve, reject };
}

test("transcript audio summary only exposes duration", () => {
  const { transcriptAudioText } = loadTranscriptsPage().mod;
  const summary = transcriptAudioText({
    audio_duration_seconds: 74,
    audio_file_id: "audio-1",
    audio_size_bytes: 491000,
    audio_mime_type: "audio/pcm",
  });

  assert.equal(summary, "时长 1 分 14 秒");
  assert.equal(summary.includes("音频已保存"), false);
  assert.equal(summary.includes("491"), false);
  assert.equal(summary.includes("PCM"), false);
});

test("transcript audio summary falls back to utterance timestamps", () => {
  const { transcriptAudioText } = loadTranscriptsPage().mod;

  assert.equal(
    transcriptAudioText({
      audio_duration_seconds: null,
      start_ms: 1000,
      end_ms: 10500,
    }),
    "时长 10 秒",
  );
});

test("transcript book inclusion text shows chapter placement", () => {
  const { transcriptBookInclusionText } = loadTranscriptsPage().mod;

  assert.equal(
    transcriptBookInclusionText({
      included_chapters: [
        { chapter_id: "chapter-2", title: "离家求学", sort_order: 1 },
        { chapter_id: "chapter-1", title: "童年院子", sort_order: 0 },
      ],
    }),
    "已放入第 1 章：童年院子等 2 章",
  );
  assert.equal(transcriptBookInclusionText({ included_chapters: [] }), "尚未入章");
});

test("transcript text highlighter marks every search term", () => {
  const { highlightTranscriptText } = loadTranscriptsPage().mod;

  assert.deepEqual(highlightTranscriptText("外婆家的院子，后来去了县城。", "外婆 县城"), [
    { text: "外婆", highlight: true },
    { text: "家的院子，后来去了", highlight: false },
    { text: "县城", highlight: true },
    { text: "。", highlight: false },
  ]);
  assert.deepEqual(highlightTranscriptText("没有搜索词", ""), [
    { text: "没有搜索词", highlight: false },
  ]);
});

test("transcript voice edit diff marks inline replacements, insertions, and deletions", () => {
  const { inlineTranscriptVoiceEditDiffSegments } = loadTranscriptsPage().mod;

  assert.deepEqual(
    inlineTranscriptVoiceEditDiffSegments(
      "他叫李月，我们是高中同学。",
      "他叫李阅，我们是高中同学。",
      [{ before: "李月", after: "李阅", reason: "用户明确纠正名字" }],
    ),
    [
      { text: "他叫", kind: "equal" },
      { text: "李月", kind: "delete" },
      { text: "李阅", kind: "insert" },
      { text: "，我们是高中同学。", kind: "equal" },
    ],
  );
  assert.deepEqual(
    inlineTranscriptVoiceEditDiffSegments("我去成都。", "我后来去成都。", []),
    [
      { text: "我", kind: "equal" },
      { text: "后来", kind: "insert" },
      { text: "去成都。", kind: "equal" },
    ],
  );
  assert.deepEqual(
    inlineTranscriptVoiceEditDiffSegments("我那天真的很开心。", "我那天很开心。", []),
    [
      { text: "我那天", kind: "equal" },
      { text: "真的", kind: "delete" },
      { text: "很开心。", kind: "equal" },
    ],
  );
});

test("transcripts view patch keeps empty states mutually exclusive", () => {
  const { mod, page } = loadTranscriptsPage();
  const baseData = { ...page.data };
  const emptyFlags = (patch) => {
    const viewPatch = mod.transcriptsViewPatch({ ...baseData, ...patch });
    return {
      showNoProjectPanel: viewPatch.showNoProjectPanel,
      showLoadingEmpty: viewPatch.showLoadingEmpty,
      showSearchEmpty: viewPatch.showSearchEmpty,
      showGenericEmpty: viewPatch.showGenericEmpty,
    };
  };

  assert.deepEqual(emptyFlags({ loading: true, activeProjectId: "" }), {
    showNoProjectPanel: false,
    showLoadingEmpty: true,
    showSearchEmpty: false,
    showGenericEmpty: false,
  });
  assert.deepEqual(emptyFlags({ loading: false, activeProjectId: "" }), {
    showNoProjectPanel: true,
    showLoadingEmpty: false,
    showSearchEmpty: false,
    showGenericEmpty: false,
  });
  assert.deepEqual(emptyFlags({ loading: false, activeProjectId: "project-1", hasSearchCriteria: true }), {
    showNoProjectPanel: false,
    showLoadingEmpty: false,
    showSearchEmpty: true,
    showGenericEmpty: false,
  });
  assert.deepEqual(emptyFlags({ loading: false, activeProjectId: "project-1", hasSearchCriteria: false }), {
    showNoProjectPanel: false,
    showLoadingEmpty: false,
    showSearchEmpty: false,
    showGenericEmpty: true,
  });
  assert.deepEqual(emptyFlags({ loading: false, needsLogin: true, activeProjectId: "" }), {
    showNoProjectPanel: false,
    showLoadingEmpty: false,
    showSearchEmpty: false,
    showGenericEmpty: false,
  });
});

test("transcripts page collapses long raw and corrected text", async () => {
  const longRaw = "院子里的年节记忆".repeat(24);
  const longDisplay = "春节时一家人围坐说笑".repeat(24);
  const { page } = loadTranscriptsPage({
    authExports: {
      getMemoirIdentity: async () => ({
        id: "user-1",
        active_project_id: "project-1",
        active_project: { id: "project-1", title: "我的回忆录" },
        projects: [],
      }),
    },
    voiceApiExports: {
      listMemoirVoiceTranscripts: async () => ({
        items: [
          {
            ...sampleTranscript("utterance-long"),
            transcript_raw: longRaw,
            transcript_edited: "",
            display_text: longDisplay,
            raw_char_count: longRaw.length,
            display_char_count: longDisplay.length,
          },
        ],
        total: 1,
        limit: 30,
        offset: 0,
      }),
    },
  });
  const context = pageContext(page);

  await page.loadTranscripts.call(context, true);

  let item = context.data.items[0];
  assert.equal(item.detailsCollapsible, true);
  assert.equal(item.detailsExpanded, false);
  assert.equal(item.detailsToggleText, "展开全部");
  assert.equal(item.rawSegments.map((segment) => segment.text).join("").endsWith("..."), true);
  assert.equal(item.displaySegments.map((segment) => segment.text).join("").length, 183);

  page.toggleTranscriptText.call(context, {
    currentTarget: { dataset: { id: "utterance-long" } },
  });

  item = context.data.items[0];
  assert.equal(item.detailsExpanded, true);
  assert.equal(item.detailsToggleText, "收起");
  assert.equal(item.rawSegments.map((segment) => segment.text).join(""), longRaw);
  assert.equal(item.displaySegments.map((segment) => segment.text).join(""), longDisplay);
});

test("transcripts page formats record time without timezone label", async () => {
  const { page } = loadTranscriptsPage({
    authExports: {
      getMemoirIdentity: async () => ({
        id: "user-1",
        active_project_id: "project-1",
        active_project: { id: "project-1", title: "我的回忆录" },
        projects: [],
      }),
    },
    voiceApiExports: {
      listMemoirVoiceTranscripts: async () => ({
        items: [sampleTranscript("utterance-1", "2026-06-17T00:00:00.000Z")],
        total: 1,
        limit: 30,
        offset: 0,
      }),
    },
  });
  const context = pageContext(page);

  await page.loadTranscripts.call(context, true);

  assert.equal(context.data.items[0].createdAtText, "06-17 08:00");
  assert.equal(context.data.items[0].recordNumber, 1);
});

test("transcripts page uses summary fields for browse cards", async () => {
  const { page } = loadTranscriptsPage({
    authExports: {
      getMemoirIdentity: async () => ({
        id: "user-1",
        active_project_id: "project-1",
        active_project: { id: "project-1", title: "我的回忆录" },
        projects: [],
      }),
    },
    voiceApiExports: {
      listMemoirVoiceTranscripts: async () => ({
        items: [
          {
            ...sampleTranscript("utterance-summary", "2026-07-01T07:38:00.000Z"),
            summary_title: "爷爷退休后仍帮公司做事",
            summary_text: "讲述者提到爷爷退休后仍继续帮公司处理事务，大家都很信任他。",
            summary_source: "source_card",
            audio_duration_seconds: 32,
          },
        ],
        total: 1,
        limit: 30,
        offset: 0,
      }),
    },
  });
  const context = pageContext(page);

  await page.loadTranscripts.call(context, true);

  assert.equal(context.data.items[0].summaryTitleText, "爷爷退休后仍帮公司做事");
  assert.equal(context.data.items[0].summaryPreviewText, "讲述者提到爷爷退休后仍继续帮公司处理事务，大家都很信任他。");
  assert.equal(context.data.items[0].recordMetaText, "07-01 15:38 · 32 秒");
  assert.equal(context.data.items[0].bookInclusionText, "尚未入章");
  assert.equal(context.data.pageMetaText, "我的回忆录 · 1 条讲述");
  assert.equal(context.data.showNoProjectPanel, false);
  assert.equal(context.data.showGenericEmpty, false);
  assert.equal(context.data.showLoadMore, false);
  assert.equal(context.data.items[0].cardClass, "transcript-card");
  assert.equal(context.data.items[0].bookStatusClass, "book-status");
  assert.equal(context.data.items[0].deleteButtonDisabled, false);
  assert.equal(context.data.items[0].deleteButtonText, "删除");
  assert.equal(context.data.items[0].editButtonDisabled, false);
});

test("transcripts page shows saved audio awaiting transcription", async () => {
  const recoverCalls = [];
  const toasts = [];
  const { page } = loadTranscriptsPage({
    authExports: {
      getMemoirIdentity: async () => ({
        id: "user-1",
        active_project_id: "project-1",
        active_project: { id: "project-1", title: "我的回忆录" },
        projects: [],
      }),
    },
    voiceApiExports: {
      listMemoirVoiceTranscripts: async () => ({
        items: [
          {
            ...sampleTranscript("utterance-pending", "2026-07-01T07:38:00.000Z"),
            transcript_raw: "",
            transcript_edited: "",
            display_text: "",
            summary_title: "音频已保存，等待转写",
            summary_text: "这段录音已经保存，但还没有识别成文字。",
            summary_source: "pending_transcription",
            raw_char_count: 0,
            edited_char_count: 0,
            display_char_count: 0,
            transcription_status: "pending_transcription",
            can_recover_transcription: true,
          },
        ],
        total: 1,
        limit: 30,
        offset: 0,
      }),
      recoverMemoirVoiceTranscript: async (utteranceId) => {
        recoverCalls.push(utteranceId);
        return {
          ...sampleTranscript(utteranceId, "2026-07-01T07:38:00.000Z"),
          transcript_raw: "爷爷退休后还在帮公司做事。",
          display_text: "爷爷退休后还在帮公司做事。",
          summary_title: "爷爷退休后仍帮公司做事",
          summary_text: "讲述者提到爷爷退休后还在帮公司做事。",
          summary_source: "source_card",
          raw_char_count: 14,
          display_char_count: 14,
          transcription_status: "ready",
          can_recover_transcription: false,
        };
      },
    },
  });
  const context = pageContext(page);

  await page.loadTranscripts.call(context, true);

  const item = context.data.items[0];
  assert.equal(item.summaryTitleText, "音频已保存，等待转写");
  assert.equal(item.summaryPreviewText, "这段录音已经保存，但还没有识别成文字。");
  assert.equal(item.transcriptionStatusText, "音频已保存");
  assert.equal(item.recordCountText, "待转写");
  assert.equal(item.recordCountClass, "record-count pending");
  assert.equal(item.bookInclusionText, "等待转写后再入章");
  assert.equal(item.bookStatusClass, "book-status pending");
  assert.equal(item.cardClass, "transcript-card recovery-pending");
  assert.equal(item.canAssignChapterSource, false);
  assert.equal(item.canMoveChapterSource, false);
  assert.equal(item.editButtonDisabled, true);

  const navigations = [];
  global.wx = {
    navigateTo(options) {
      navigations.push(options.url);
    },
    showToast(options) {
      toasts.push(options);
    },
  };
  page.openTranscriptEdit.call(context, {
    currentTarget: { dataset: { id: "utterance-pending" } },
  });
  assert.deepEqual(navigations, []);

  await page.recoverTranscript.call(context, {
    currentTarget: { dataset: { id: "utterance-pending" } },
  });

  assert.deepEqual(recoverCalls, ["utterance-pending"]);
  assert.equal(context.data.items[0].canRecoverTranscription, false);
  assert.equal(context.data.items[0].recordCountText, "14 字");
  assert.equal(context.data.items[0].summaryTitleText, "爷爷退休后仍帮公司做事");
  assert.deepEqual(toasts, [{ title: "已恢复转写", icon: "success" }]);
  delete global.wx;
});

test("transcripts page deletes a record from the first-screen list action", async () => {
  const deleted = [];
  const modals = [];
  const toasts = [];
  global.wx = {
    showModal(options) {
      modals.push(options);
      options.success({ confirm: true });
    },
    showToast(options) {
      toasts.push(options);
    },
  };
  const { page } = loadTranscriptsPage({
    authExports: {
      getMemoirIdentity: async () => ({
        id: "user-1",
        active_project_id: "project-1",
        active_project: { id: "project-1", title: "我的回忆录" },
        projects: [],
      }),
    },
    voiceApiExports: {
      listMemoirVoiceTranscripts: async () => ({
        items: [sampleTranscript("utterance-delete")],
        total: 1,
        limit: 30,
        offset: 0,
      }),
      deleteMemoirVoiceTranscript: async (id) => {
        deleted.push(id);
      },
    },
  });
  const context = pageContext(page);

  await page.loadTranscripts.call(context, true);
  assert.equal(context.data.items[0].deleteButtonText, "删除");

  await page.deleteTranscript.call(context, {
    currentTarget: { dataset: { id: "utterance-delete" } },
  });

  assert.deepEqual(deleted, ["utterance-delete"]);
  assert.match(modals[0].content, /这条原始讲述会从素材库移除/);
  assert.equal(context.data.items.length, 0);
  assert.equal(context.data.total, 0);
  assert.deepEqual(toasts, [{ title: "已删除", icon: "success" }]);
  delete global.wx;
});

test("transcripts page opens transcript detail from record header", () => {
  const navigations = [];
  global.wx = {
    navigateTo(options) {
      navigations.push(options);
    },
  };
  const { page } = loadTranscriptsPage();
  const context = pageContext(page);

  page.openTranscriptDetail.call(context, {
    currentTarget: { dataset: { id: "utterance/detail id" } },
  });

  assert.deepEqual(navigations, [
    { url: "/pages/transcript-detail/index?id=utterance%2Fdetail%20id" },
  ]);

  page.openTranscriptEdit.call(context, {
    currentTarget: { dataset: { id: "utterance/edit id" } },
  });

  assert.deepEqual(navigations[1], {
    url: "/pages/transcript-detail/index?id=utterance%2Fedit%20id&mode=edit",
  });
  delete global.wx;
});

test("transcripts page numbers newest-first records from the oldest record", async () => {
  const { page } = loadTranscriptsPage({
    authExports: {
      getMemoirIdentity: async () => ({
        id: "user-1",
        active_project_id: "project-1",
        active_project: { id: "project-1", title: "我的回忆录" },
        projects: [],
      }),
    },
    voiceApiExports: {
      listMemoirVoiceTranscripts: async ({ offset }) => ({
        items: offset === 0
          ? [
            sampleTranscript("utterance-newest", "2026-06-17T02:00:00.000Z"),
            sampleTranscript("utterance-middle", "2026-06-17T01:00:00.000Z"),
          ]
          : [
            sampleTranscript("utterance-oldest", "2026-06-17T00:00:00.000Z"),
          ],
        total: 3,
        limit: 2,
        offset,
      }),
    },
  });
  const context = pageContext(page, { limit: 2 });

  await page.loadTranscripts.call(context, true);
  await page.loadMore.call(context);

  assert.deepEqual(context.data.items.map((item) => item.id), [
    "utterance-newest",
    "utterance-middle",
    "utterance-oldest",
  ]);
  assert.deepEqual(context.data.items.map((item) => item.recordNumber), [3, 2, 1]);
});

test("transcripts page preserves loaded records after returning from detail", async () => {
  const calls = [];
  const navigations = [];
  const records = [
    sampleTranscript("utterance-1", "2026-06-17T04:00:00.000Z"),
    sampleTranscript("utterance-2", "2026-06-17T03:00:00.000Z"),
    sampleTranscript("utterance-3", "2026-06-17T02:00:00.000Z"),
    sampleTranscript("utterance-4", "2026-06-17T01:00:00.000Z"),
    sampleTranscript("utterance-5", "2026-06-17T00:00:00.000Z"),
  ];
  global.wx = {
    getStorageSync() {
      return "";
    },
    navigateTo(options) {
      navigations.push(options);
    },
  };
  const { page } = loadTranscriptsPage({
    authExports: {
      getMemoirIdentity: async () => ({
        id: "user-1",
        active_project_id: "project-1",
        active_project: { id: "project-1", title: "我的回忆录" },
        projects: [],
      }),
    },
    voiceApiExports: {
      listMemoirVoiceTranscripts: async ({ limit, offset }) => {
        calls.push({ limit, offset });
        return {
          items: records.slice(offset, offset + limit),
          total: records.length,
          limit,
          offset,
        };
      },
    },
  });
  const context = pageContext(page, { limit: 2 });

  await page.loadTranscripts.call(context, true);
  await page.loadMore.call(context);
  page.openTranscriptDetail.call(context, {
    currentTarget: { dataset: { id: "utterance-4" } },
  });
  await page.onShow.call(context);

  assert.deepEqual(calls, [
    { limit: 2, offset: 0 },
    { limit: 2, offset: 2 },
    { limit: 4, offset: 0 },
  ]);
  assert.deepEqual(navigations, [
    { url: "/pages/transcript-detail/index?id=utterance-4" },
  ]);
  assert.deepEqual(context.data.items.map((item) => item.id), [
    "utterance-1",
    "utterance-2",
    "utterance-3",
    "utterance-4",
  ]);
  assert.equal(context.data.showLoadMore, true);
  delete global.wx;
});

test("transcripts page moves an included source to another chapter", async () => {
  const moves = [];
  const toasts = [];
  global.wx = {
    showToast(options) {
      toasts.push(options);
    },
    switchTab() {},
  };
  const { page } = loadTranscriptsPage({
    authExports: {
      getMemoirIdentity: async () => ({
        id: "user-1",
        active_project_id: "project-1",
        active_project: { id: "project-1", title: "我的回忆录" },
        projects: [],
      }),
    },
    voiceApiExports: {
      getMemoirBookPlan: async () => sampleBookPlan(),
      listMemoirVoiceTranscripts: async () => ({
        items: [
          {
            ...sampleTranscript("utterance-2"),
            is_included_in_book: true,
            included_chapters: [{ chapter_id: "chapter-1", title: "岁首欢聚记", sort_order: 0 }],
          },
        ],
        total: 1,
        limit: 30,
        offset: 0,
      }),
      moveMemoirBookSource: async (projectId, data) => {
        moves.push({ projectId, data });
        return {
          ...sampleBookPlan(),
          chapters: [
            {
              ...sampleBookPlan().chapters[0],
              source_refs: [sampleSourceRef("utterance-1")],
            },
            {
              ...sampleBookPlan().chapters[1],
              source_refs: [sampleSourceRef("utterance-3"), sampleSourceRef("utterance-2")],
            },
          ],
        };
      },
    },
  });
  const context = pageContext(page);

  await page.loadTranscripts.call(context, true);
  assert.equal(context.data.items[0].canMoveChapterSource, true);
  assert.deepEqual(context.data.items[0].chapterMoveOptions, [
    { label: "第 2 章 童年友情", value: "chapter-2" },
  ]);

  await page.moveTranscriptChapter.call(context, {
    currentTarget: { dataset: { id: "utterance-2" } },
    detail: { value: 0 },
  });

  assert.deepEqual(moves, [
    {
      projectId: "project-1",
      data: {
        source_chapter_id: "chapter-1",
        target_chapter_id: "chapter-2",
        utterance_id: "utterance-2",
      },
    },
  ]);
  assert.deepEqual(toasts, [{ title: "素材已移动", icon: "success" }]);
  delete global.wx;
});

test("transcripts page assigns an uncovered source to a chapter", async () => {
  const assignments = [];
  const toasts = [];
  global.wx = {
    showToast(options) {
      toasts.push(options);
    },
    switchTab() {},
  };
  const { page } = loadTranscriptsPage({
    authExports: {
      getMemoirIdentity: async () => ({
        id: "user-1",
        active_project_id: "project-1",
        active_project: { id: "project-1", title: "我的回忆录" },
        projects: [],
      }),
    },
    voiceApiExports: {
      getMemoirBookPlan: async () => sampleBookPlan(),
      listMemoirVoiceTranscripts: async () => ({
        items: [sampleTranscript("utterance-new")],
        total: 1,
        limit: 30,
        offset: 0,
      }),
      assignMemoirBookSources: async (projectId, data) => {
        assignments.push({ projectId, data });
        return sampleBookPlan();
      },
    },
  });
  const context = pageContext(page);

  await page.loadTranscripts.call(context, true);
  assert.equal(context.data.items[0].canAssignChapterSource, true);
  assert.deepEqual(context.data.items[0].chapterAssignOptions, [
    { label: "第 1 章 岁首欢聚记", value: "chapter-1" },
    { label: "第 2 章 童年友情", value: "chapter-2" },
  ]);

  await page.assignTranscriptChapter.call(context, {
    currentTarget: { dataset: { id: "utterance-new" } },
    detail: { value: 0 },
  });

  assert.deepEqual(assignments, [
    {
      projectId: "project-1",
      data: { assignments: [{ chapter_id: "chapter-1", utterance_ids: ["utterance-new"] }] },
    },
  ]);
  assert.deepEqual(toasts, [{ title: "已归入章节", icon: "success" }]);
  delete global.wx;
});

test("transcripts page searches records and clears back to the full list", async () => {
  const calls = [];
  const { page } = loadTranscriptsPage({
    authExports: {
      getMemoirIdentity: async () => ({
        id: "user-1",
        active_project_id: "project-1",
        active_project: { id: "project-1", title: "我的回忆录" },
        projects: [],
      }),
    },
    voiceApiExports: {
      listMemoirVoiceTranscripts: async (options) => {
        calls.push(options);
        if (options.query) {
          return {
            items: [
              {
                ...sampleTranscript("utterance-hit"),
                transcript_raw: "外婆家的院子",
                transcript_edited: "",
                display_text: "外婆家的院子",
                raw_char_count: 6,
                display_char_count: 6,
              },
            ],
            total: 1,
            limit: 30,
            offset: 0,
          };
        }
        return {
          items: [sampleTranscript("utterance-all")],
          total: 8,
          limit: 30,
          offset: 0,
        };
      },
    },
  });
  const context = pageContext(page);

  await page.loadTranscripts.call(context, true);
  context.setData({ searchInput: " 外婆 " });
  await page.onSearchConfirm.call(context);
  await page.clearSearch.call(context);

  assert.deepEqual(calls.map((item) => item.query), [undefined, "外婆", undefined]);
  assert.equal(context.data.searchQuery, "");
  assert.equal(context.data.searchResultText, "");
  assert.equal(context.data.items[0].id, "utterance-all");
});

test("transcripts page stores highlighted search segments on matching results", async () => {
  const { page } = loadTranscriptsPage({
    authExports: {
      getMemoirIdentity: async () => ({
        id: "user-1",
        active_project_id: "project-1",
        active_project: { id: "project-1", title: "我的回忆录" },
        projects: [],
      }),
    },
    voiceApiExports: {
      listMemoirVoiceTranscripts: async () => ({
        items: [
          {
            ...sampleTranscript("utterance-hit"),
            question_text: "你在县城读书时住在哪里？",
            transcript_raw: "我在县城读书。",
            transcript_edited: "",
            display_text: "我在县城读书。",
          },
        ],
        total: 1,
        limit: 30,
        offset: 0,
      }),
    },
  });
  const context = pageContext(page, { searchQuery: "县城" });

  await page.loadTranscripts.call(context, true);

  assert.deepEqual(context.data.items[0].questionSegments.slice(0, 2), [
    { text: "你在", highlight: false },
    { text: "县城", highlight: true },
  ]);
  assert.deepEqual(context.data.items[0].displaySegments.slice(0, 2), [
    { text: "我在", highlight: false },
    { text: "县城", highlight: true },
  ]);
  assert.equal(context.data.searchResultText, "找到 1 条相关记录");
});

test("transcripts page ignores stale search responses", async () => {
  const firstSearch = deferred();
  const secondSearch = deferred();
  const calls = [];
  const { page } = loadTranscriptsPage({
    authExports: {
      getMemoirIdentity: async () => ({
        id: "user-1",
        active_project_id: "project-1",
        active_project: { id: "project-1", title: "我的回忆录" },
        projects: [],
      }),
    },
    voiceApiExports: {
      listMemoirVoiceTranscripts: async (options) => {
        calls.push(options.query);
        if (options.query === "外婆") {
          return firstSearch.promise;
        }
        if (options.query === "县城") {
          return secondSearch.promise;
        }
        return { items: [], total: 0, limit: 30, offset: 0 };
      },
    },
  });
  const context = pageContext(page);

  const firstPromise = page.applySearch.call(context, "外婆");
  const secondPromise = page.applySearch.call(context, "县城");
  secondSearch.resolve({
    items: [sampleTranscript("utterance-county")],
    total: 1,
    limit: 30,
    offset: 0,
  });
  await secondPromise;
  firstSearch.resolve({
    items: [sampleTranscript("utterance-grandma")],
    total: 1,
    limit: 30,
    offset: 0,
  });
  await firstPromise;

  assert.deepEqual(calls, ["外婆", "县城"]);
  assert.equal(context.data.searchQuery, "县城");
  assert.equal(context.data.items[0].id, "utterance-county");
  assert.equal(context.data.loading, false);
});

test("transcripts page applies recognized voice search text", async () => {
  const calls = [];
  const parseCalls = [];
  const toasts = [];
  global.wx = {
    showToast(options) {
      toasts.push(options);
    },
  };
  const { page } = loadTranscriptsPage({
    authExports: {
      getMemoirIdentity: async () => ({
        id: "user-1",
        active_project_id: "project-1",
        active_project: { id: "project-1", title: "我的回忆录" },
        projects: [],
      }),
    },
    voiceSearchExports: {
      recordMemoirVoiceSearchQuery: async (options) => {
        options.onRecordingEnd();
        return "帮我找未收录的外婆家院子，地点相关";
      },
    },
    voiceApiExports: {
      listMemoirVoiceTranscripts: async (options) => {
        calls.push(options);
        return {
          items: [sampleTranscript("utterance-hit")],
          total: 1,
          limit: 30,
          offset: 0,
        };
      },
      parseMemoirVoiceSearchQuery: async (text) => {
        parseCalls.push(text);
        return {
          original_text: text,
          query: "外婆家院子",
          keywords: ["外婆", "院子"],
          filters: { included: "not_included", guidance_mode: null, module: "place" },
          display_text: "外婆家院子 · 未收录 · 地点",
          used_ai: true,
          provider: "fake-ai",
          model: "fake-model",
        };
      },
    },
  });
  const context = pageContext(page);

  await page.startVoiceSearch.call(context);

  assert.deepEqual(parseCalls, ["帮我找未收录的外婆家院子，地点相关"]);
  assert.equal(context.data.searchInput, "外婆家院子");
  assert.equal(context.data.searchQuery, "外婆家院子");
  assert.deepEqual(calls.map((item) => item.query), ["外婆家院子"]);
  assert.deepEqual(calls.map((item) => item.included), ["not_included"]);
  assert.deepEqual(calls.map((item) => item.module), ["place"]);
  assert.deepEqual(context.data.searchChips, [{ text: "外婆" }, { text: "院子" }, { text: "未收录" }, { text: "地点" }]);
  assert.equal(context.data.searchDisplayText, "外婆家院子 · 未收录 · 地点");
  assert.equal(context.data.voiceSearchRecording, false);
  assert.equal(context.data.voiceSearchTranscribing, false);
  assert.deepEqual(toasts, [{ title: "已识别", icon: "success" }]);
  delete global.wx;
});

test("transcripts page supports filter-only parsed voice search", async () => {
  const calls = [];
  const { page } = loadTranscriptsPage({
    authExports: {
      getMemoirIdentity: async () => ({
        id: "user-1",
        active_project_id: "project-1",
        active_project: { id: "project-1", title: "我的回忆录" },
        projects: [],
      }),
    },
    voiceApiExports: {
      listMemoirVoiceTranscripts: async (options) => {
        calls.push(options);
        return {
          items: [sampleTranscript("utterance-filtered")],
          total: options.included === "not_included" ? 1 : 8,
          limit: 30,
          offset: 0,
        };
      },
    },
  });
  const context = pageContext(page);

  await page.applySearch.call(
    context,
    "",
    { included: "not_included", module: "work" },
    {
      original_text: "帮我找工作相关的未收录记录",
      query: "",
      keywords: [],
      filters: { included: "not_included", guidance_mode: null, module: "work" },
      display_text: "未收录 · 工作",
      used_ai: false,
    },
  );
  await page.clearSearch.call(context);

  assert.deepEqual(calls.map((item) => item.query), [undefined, undefined]);
  assert.deepEqual(calls.map((item) => item.included), ["not_included", "all"]);
  assert.deepEqual(calls.map((item) => item.module), ["work", undefined]);
  assert.equal(context.data.hasSearchCriteria, false);
  assert.deepEqual(context.data.searchChips, []);
});

test("transcripts page stops active voice search recording on second tap", async () => {
  const stops = [];
  const { page } = loadTranscriptsPage({
    voiceSearchExports: {
      stopMemoirVoiceSearchRecording: () => stops.push("stop"),
    },
  });
  const context = pageContext(page, { voiceSearchRecording: true });

  await page.startVoiceSearch.call(context);

  assert.deepEqual(stops, ["stop"]);
});

test("transcripts page previews and saves a voice transcript correction", async () => {
  const previewCalls = [];
  const updateCalls = [];
  const toasts = [];
  global.wx = {
    showToast(options) {
      toasts.push(options);
    },
  };
  const { page } = loadTranscriptsPage({
    authExports: {
      getMemoirIdentity: async () => ({
        id: "user-1",
        active_project_id: "project-1",
        active_project: { id: "project-1", title: "我的回忆录" },
        projects: [],
      }),
    },
    voiceApiExports: {
      listMemoirVoiceTranscripts: async () => ({
        items: [sampleTranscript("utterance-edit")],
        total: 1,
        limit: 30,
        offset: 0,
      }),
      previewMemoirVoiceTranscriptVoiceEdit: async (utteranceId, instructionText) => {
        previewCalls.push({ utteranceId, instructionText });
        return {
          utterance_id: utteranceId,
          action: "revise_transcript",
          original_text: "我小时候住在外婆家。",
          current_text: "我小时候住在外婆家。",
          edited_text: "我小时候住在奶奶家。",
          changes: [{ before: "外婆", after: "奶奶", reason: "用户明确纠正人物" }],
          confidence: 0.93,
          requires_confirmation: true,
          reply_text: "已把外婆改成奶奶，请确认后保存。",
          provider: "fake-ai",
          model: "fake-model",
        };
      },
      updateMemoirVoiceTranscript: async (utteranceId, content) => {
        updateCalls.push({ utteranceId, content });
        return {
          ...sampleTranscript(utteranceId),
          transcript_raw: "我小时候住在外婆家。",
          transcript_edited: content,
          display_text: content,
          edited_char_count: content.length,
          display_char_count: content.length,
        };
      },
    },
    voiceSearchExports: {
      recordMemoirVoiceSearchQuery: async (options) => {
        options.onRecordingEnd();
        return "这里不是外婆，是奶奶";
      },
    },
  });
  const context = pageContext(page);

  await page.loadTranscripts.call(context, true);
  await page.startVoiceEdit.call(context, {
    currentTarget: { dataset: { id: "utterance-edit" } },
  });

  assert.deepEqual(previewCalls, [
    { utteranceId: "utterance-edit", instructionText: "这里不是外婆，是奶奶" },
  ]);
  assert.equal(context.data.voiceEditTargetId, "utterance-edit");
  assert.equal(context.data.voiceEditInstructionText, "这里不是外婆，是奶奶");
  assert.equal(context.data.voiceEditPreview.editedText, "我小时候住在奶奶家。");
  assert.equal(context.data.voiceEditPreview.canConfirm, true);
  assert.equal(context.data.voiceEditPreview.confidenceText, "93%");
  assert.deepEqual(context.data.voiceEditPreview.diffSegments, [
    { text: "我小时候住在", kind: "equal" },
    { text: "外婆", kind: "delete" },
    { text: "奶奶", kind: "insert" },
    { text: "家。", kind: "equal" },
  ]);

  await page.confirmVoiceEdit.call(context);

  assert.deepEqual(updateCalls, [
    { utteranceId: "utterance-edit", content: "我小时候住在奶奶家。" },
  ]);
  assert.equal(context.data.items[0].display_text, "我小时候住在奶奶家。");
  assert.equal(context.data.voiceEditTargetId, "");
  assert.deepEqual(toasts, [{ title: "已保存修正", icon: "success" }]);
  delete global.wx;
});

test("transcripts page blocks voice search while a transcript correction is recording", async () => {
  const stops = [];
  const { page } = loadTranscriptsPage({
    voiceSearchExports: {
      stopMemoirVoiceSearchRecording: () => stops.push("stop"),
    },
  });
  const context = pageContext(page, {
    voiceEditRecording: true,
    voiceEditTargetId: "utterance-edit",
  });

  await page.startVoiceSearch.call(context);

  assert.deepEqual(stops, []);
  assert.equal(context.data.error, "请先完成当前语音修正");
});

test("transcripts page ignores stale voice correction results after cancel", async () => {
  const previewCalls = [];
  let resolveRecording;
  const { page } = loadTranscriptsPage({
    voiceApiExports: {
      previewMemoirVoiceTranscriptVoiceEdit: async (...args) => {
        previewCalls.push(args);
        return {};
      },
    },
    voiceSearchExports: {
      recordMemoirVoiceSearchQuery: async () => new Promise((resolve) => {
        resolveRecording = resolve;
      }),
      stopMemoirVoiceSearchRecording: () => {},
    },
  });
  const context = pageContext(page, {
    items: [sampleTranscript("utterance-edit")],
  });

  const pending = page.startVoiceEdit.call(context, {
    currentTarget: { dataset: { id: "utterance-edit" } },
  });
  page.cancelVoiceEdit.call(context);
  resolveRecording("这里不是外婆，是奶奶");
  await pending;

  assert.deepEqual(previewCalls, []);
  assert.equal(context.data.voiceEditTargetId, "");
  assert.equal(context.data.voiceEditPreview, null);
});

test("transcripts page scrolls to stored uncovered source target", async () => {
  const scrolls = [];
  const removedKeys = [];
  global.wx = {
    getStorageSync(key) {
      if (key === "pawtale_transcript_navigation_target") {
        return { utterance_id: "utterance-target" };
      }
      return "";
    },
    removeStorageSync(key) {
      removedKeys.push(key);
    },
    pageScrollTo(options) {
      scrolls.push(options);
    },
  };
  const calls = [];
  const { page } = loadTranscriptsPage({
    authExports: {
      getMemoirIdentity: async () => ({
        id: "user-1",
        active_project_id: "project-1",
        active_project: { id: "project-1", title: "我的回忆录" },
        projects: [],
      }),
    },
    voiceApiExports: {
      listMemoirVoiceTranscripts: async ({ offset }) => {
        calls.push(offset);
        return {
          items: offset === 0
            ? [sampleTranscript("utterance-old")]
            : [sampleTranscript("utterance-target")],
          total: 2,
          limit: 1,
          offset,
        };
      },
    },
  });
  const context = pageContext(page, { limit: 1 });

  await page.onShow.call(context);

  assert.deepEqual(calls, [0, 1]);
  assert.equal(context.data.highlightedId, "utterance-target");
  assert.deepEqual(removedKeys, ["pawtale_transcript_navigation_target"]);
  assert.deepEqual(scrolls, [
    {
      selector: "#transcript-utterance-target",
      offsetTop: 16,
      duration: 240,
    },
  ]);
  delete global.wx;
});

test("transcripts page stays browseable when login is required", async () => {
  const switches = [];
  global.wx = {
    switchTab(options) {
      switches.push(options.url);
    },
  };
  const { ApiError } = require("../services/request.ts");
  const { page } = loadTranscriptsPage({
    authExports: {
      getMemoirIdentity: async () => {
        throw new ApiError(401, "请先登录", {});
      },
    },
  });
  const context = pageContext(page);

  await page.loadTranscripts.call(context, true);

  assert.equal(context.data.needsLogin, true);
  assert.equal(context.data.projectTitle, "我的回忆录");
  assert.deepEqual(context.data.items, []);
  assert.deepEqual(switches, []);
  delete global.wx;
});

test("transcripts page asks logged-in users to create a memoir before listing records", async () => {
  let listCalls = 0;
  const { page } = loadTranscriptsPage({
    authExports: {
      getMemoirIdentity: async () => ({
        id: "user-1",
        email: "memoir-author@example.com",
        password_login_enabled: true,
        display_name: "作者",
        created_at: "2026-06-15T00:00:00",
        active_project_id: "",
        active_project: null,
        projects: [],
      }),
    },
    voiceApiExports: {
      listMemoirVoiceTranscripts: async () => {
        listCalls += 1;
        return { items: [], total: 0, limit: 50, offset: 0 };
      },
    },
  });
  const context = pageContext(page);

  await page.loadTranscripts.call(context, true);

  assert.equal(context.data.needsLogin, false);
  assert.equal(context.data.activeProjectId, "");
  assert.equal(context.data.projectTitle, "尚未创建回忆录");
  assert.deepEqual(context.data.items, []);
  assert.equal(listCalls, 0);
});
