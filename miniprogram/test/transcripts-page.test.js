require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");
const {
  mockMemoirApiModules,
  loadPageModule,
  mockModule,
  pageContext,
  unmockedAsync,
} = require("./page-harness");

function loadTranscriptsPage({ authExports = {}, voiceApiExports = {}, voiceSearchExports = {} } = {}) {
  mockModule("../services/auth.ts", {
    getMemoirIdentity: unmockedAsync("getMemoirIdentity"),
    ...authExports,
  });
  mockMemoirApiModules({
    listMemoirVoiceTranscripts: unmockedAsync("listMemoirVoiceTranscripts"),
    parseMemoirVoiceSearchQuery: unmockedAsync("parseMemoirVoiceSearchQuery"),
    recoverMemoirVoiceTranscript: unmockedAsync("recoverMemoirVoiceTranscript"),
    ...voiceApiExports,
  });
  mockModule("../services/memoir-voice-search.ts", {
    recordMemoirVoiceSearchQuery: unmockedAsync("recordMemoirVoiceSearchQuery"),
    stopMemoirVoiceSearchRecording: () => {},
    cancelMemoirVoiceSearchRecording: () => {},
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
    section_assignment: null,
    created_at: createdAt,
    session_created_at: "2026-06-17T00:00:00.000Z",
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

test("transcripts load more action stays inactive while a page request is running", async () => {
  let loadCalls = 0;
  const { page } = loadTranscriptsPage();
  const context = {
    ...pageContext(page, {
      loading: true,
      items: [],
      total: 1,
    }),
    async loadTranscripts() {
      loadCalls += 1;
    },
  };

  await page.loadMore.call(context);

  assert.equal(loadCalls, 0);
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

  assert.equal(context.data.items[0].recordMetaText, "06-17 08:00 · 8 秒 · 5 字");
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
            summary_source: "fallback",
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
  assert.equal(context.data.items[0].recordMetaText, "07-01 15:38 · 32 秒 · 5 字");
  assert.equal(context.data.items[0].storyStatusText, "尚未整理");
  assert.equal(context.data.pageMetaText, "我的回忆录 · 1 条讲述");
  assert.equal(context.data.showNoProjectPanel, false);
  assert.equal(context.data.showGenericEmpty, false);
  assert.equal(context.data.showLoadMore, false);
  assert.equal(context.data.items[0].cardClass, "transcript-card");
  assert.match(context.data.items[0].cardAriaLabel, /爷爷退休后仍帮公司做事/);
  assert.match(context.data.items[0].cardAriaLabel, /打开详情$/);
  assert.equal("transcript_raw" in context.data.items[0], false);
});

test("transcripts page shows the assigned section on a material card", async () => {
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
        items: [{
          ...sampleTranscript("utterance-assigned"),
          is_included_in_book: true,
          included_chapters: [{
            chapter_id: "chapter-1",
            title: "家里的人",
            sort_order: 0,
          }],
          section_assignment: {
            chapter_id: "chapter-1",
            chapter_title: "家里的人",
            chapter_sort_order: 0,
            section_id: "section-2",
            section_title: "她给我送胡豆",
            section_sort_order: 1,
            requires_user_assignment: false,
          },
        }],
        total: 1,
        limit: 30,
        offset: 0,
      }),
    },
  });
  const context = pageContext(page);

  await page.loadTranscripts.call(context, true);

  assert.equal(
    context.data.items[0].storyStatusText,
    "已收进：她给我送胡豆",
  );
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
          summary_source: "fallback",
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
  assert.ok(item.summaryTitleText);
  assert.ok(item.summaryPreviewText);
  assert.equal(item.recordMetaText, "07-01 15:38 · 8 秒");
  assert.equal("recoveryNoticeText" in item, false);
  assert.ok(item.storyStatusText);
  assert.ok(item.recoverButtonText);
  assert.equal(item.cardClass, "transcript-card recovery-pending");
  assert.match(item.cardAriaLabel, /打开详情$/);
  global.wx = {
    showToast(options) {
      toasts.push(options);
    },
  };

  await page.recoverTranscript.call(context, {
    currentTarget: { dataset: { id: "utterance-pending" } },
  });

  assert.deepEqual(recoverCalls, ["utterance-pending"]);
  assert.equal(context.data.items[0].canRecoverTranscription, false);
  assert.equal(context.data.items[0].recordMetaText, "07-01 15:38 · 8 秒 · 14 字");
  assert.equal(context.data.items[0].summaryTitleText, "爷爷退休后仍帮公司做事");
  assert.equal(toasts.length, 1);
  assert.equal(toasts[0].icon, "success");
});

test("transcripts page opens transcript detail from a record card", () => {
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

  // Opened to pick a video clip, the same tap goes straight to sentence selection.
  global.wx.setNavigationBarTitle = () => {};
  page.onLoad.call(context, { purpose: "video" });
  page.openTranscriptDetail.call(context, {
    currentTarget: { dataset: { id: "utterance-video" } },
  });
  assert.deepEqual(navigations.at(-1), { url: "/pages/transcript-media/index?id=utterance-video" });
});

test("transcripts card swipes left to reveal deletion without opening detail, and right to close", () => {
  const navigations = [];
  global.wx = { navigateTo: (options) => navigations.push(options) };
  const { page } = loadTranscriptsPage();
  const context = pageContext(page, { items: [{ id: "utterance-1" }] });
  const start = (x, y) => page.onCardTouchStart.call(context, {
    currentTarget: { dataset: { id: "utterance-1" } }, touches: [{ pageX: x, pageY: y }],
  });
  const end = (x, y) => page.onCardTouchEnd.call(context, {
    currentTarget: { dataset: { id: "utterance-1" } }, changedTouches: [{ pageX: x, pageY: y }],
  });
  const tap = () => page.openTranscriptDetail.call(context, {
    currentTarget: { dataset: { id: "utterance-1" } },
  });

  start(210, 100);
  end(190, 180);
  assert.equal(context.data.openSwipeId, "");
  start(210, 100);
  end(110, 105);
  tap();
  assert.equal(context.data.openSwipeId, "utterance-1");
  assert.deepEqual(navigations, []);
  start(110, 100);
  end(210, 105);
  assert.equal(context.data.openSwipeId, "");
});

test("transcripts card deletion confirms, refreshes list, and retains the card on cancellation or failure", async () => {
  const records = [sampleTranscript("utterance-1"), sampleTranscript("utterance-2")];
  const deletes = [];
  const toasts = [];
  let confirm = false;
  let failDelete = false;
  global.wx = {
    showModal(options) { options.success({ confirm }); },
    showToast(options) { toasts.push(options); },
  };
  const { page } = loadTranscriptsPage({
    authExports: {
      getMemoirIdentity: async () => ({
        active_project_id: "project-1", active_project: { title: "我的回忆录" },
      }),
    },
    voiceApiExports: {
      listMemoirVoiceTranscripts: async () => ({ items: [...records], total: records.length }),
      deleteMemoirVoiceTranscript: async (id) => {
        deletes.push(id);
        if (failDelete) throw new Error("删除暂时失败");
        records.splice(records.findIndex((item) => item.id === id), 1);
      },
    },
  });
  const context = pageContext(page);
  const event = { currentTarget: { dataset: { id: "utterance-1" } } };
  await page.loadTranscripts.call(context, true);
  context.setData({ openSwipeId: "utterance-1" });

  await page.deleteTranscript.call(context, event);
  assert.deepEqual(deletes, []);
  assert.equal(context.data.openSwipeId, "utterance-1");

  confirm = true;
  failDelete = true;
  await page.deleteTranscript.call(context, event);
  assert.deepEqual(deletes, ["utterance-1"]);
  assert.equal(context.data.items.length, 2);
  assert.equal(context.data.error, "删除暂时失败");

  failDelete = false;
  await page.deleteTranscript.call(context, event);
  assert.deepEqual(deletes, ["utterance-1", "utterance-1"]);
  assert.deepEqual(context.data.items.map((item) => item.id), ["utterance-2"]);
  assert.equal(context.data.total, 1);
  assert.equal(context.data.openSwipeId, "");
  assert.deepEqual(toasts, [{ title: "已删除", icon: "success" }]);
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

test("material tabs reset pagination while preserving search and the selected scope", async () => {
  const calls = [];
  const { page } = loadTranscriptsPage({
    authExports: {
      getMemoirIdentity: async () => ({ active_project_id: "project-1", active_project: { title: "我的回忆录" } }),
    },
    voiceApiExports: {
      listMemoirVoiceTranscripts: async (options) => {
        calls.push(options);
        return { items: [sampleTranscript(`item-${options.offset}`)], total: 3, limit: options.limit, offset: options.offset };
      },
    },
  });
  const context = pageContext(page, { activeProjectId: "project-1", limit: 1, offset: 5,
    searchInput: "外婆", searchQuery: "外婆", searchFilters: { included: "not_included", narrationMode: "", module: "" } });
  const selectTab = (tab) => page.selectMaterialTab.call(context, { currentTarget: { dataset: { tab } } });

  await selectTab("organized");
  await page.loadMore.call(context);
  assert.deepEqual(context.data.items.map((item) => item.id), ["item-0", "item-1"]);
  await selectTab("unorganized");
  await selectTab("unorganized");
  await page.clearSearch.call(context);

  assert.deepEqual(calls.map(({ organized, offset, query, included }) => ({ organized, offset, query, included })), [
    { organized: true, offset: 0, query: "外婆", included: "not_included" },
    { organized: true, offset: 1, query: "外婆", included: "not_included" },
    { organized: false, offset: 0, query: "外婆", included: "not_included" },
    { organized: false, offset: 0, query: undefined, included: "all" },
  ]);
  assert.equal(context.data.materialTab, "unorganized");
  assert.equal(context.data.materialTabs.find((tab) => tab.selected).value, "unorganized");
  assert.deepEqual(context.data.items.map((item) => item.id), ["item-0"]);
});

test("material tabs ignore a late response from the previous tab", async () => {
  const organized = deferred();
  const requested = deferred();
  const { page } = loadTranscriptsPage({
    authExports: {
      getMemoirIdentity: async () => ({ active_project_id: "project-1" }),
    },
    voiceApiExports: {
      listMemoirVoiceTranscripts: async (options) => {
        if (options.organized) {
          requested.resolve();
          return organized.promise;
        }
        return { items: [sampleTranscript("unorganized")], total: 1, limit: 30, offset: 0 };
      },
    },
  });
  const context = pageContext(page, { activeProjectId: "project-1" });
  const first = page.selectMaterialTab.call(context, { currentTarget: { dataset: { tab: "organized" } } });
  await requested.promise;
  await page.selectMaterialTab.call(context, { currentTarget: { dataset: { tab: "unorganized" } } });
  organized.resolve({ items: [sampleTranscript("organized")], total: 1, limit: 30, offset: 0 });
  await first;

  assert.equal(context.data.materialTab, "unorganized");
  assert.equal(context.data.items[0].id, "unorganized");
  assert.equal(context.data.loading, false);
});

test("material cards distinguish pending assignment from excluded stories", async () => {
  const assignment = { chapter_id: "chapter-1", chapter_title: "童年", chapter_sort_order: 0,
    section_id: "section-1", section_title: "院子", section_sort_order: 0 };
  const { page } = loadTranscriptsPage({
    authExports: {
      getMemoirIdentity: async () => ({ active_project_id: "project-1" }),
    },
    voiceApiExports: {
      listMemoirVoiceTranscripts: async () => ({ items: [
        { ...sampleTranscript("excluded"), section_assignment: { ...assignment, requires_user_assignment: false } },
        { ...sampleTranscript("pending"), section_assignment: { ...assignment, requires_user_assignment: true } },
      ], total: 2, limit: 30, offset: 0 }),
    },
  });
  const context = pageContext(page);
  await page.loadTranscripts.call(context, true);

  assert.equal(context.data.items[0].storyStatusText, "已收进：院子");
  assert.equal(context.data.items[1].storyStatusText, "尚未整理");
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

  assert.deepEqual(context.data.items[0].summarySegments.slice(0, 2), [
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

test("transcripts page cancels voice search on hide or unload and ignores its late result", async () => {
  for (const lifecycle of ["onHide", "onUnload"]) {
    const recording = deferred();
    const cancels = [];
    const parseCalls = [];
    const { page } = loadTranscriptsPage({
      voiceSearchExports: {
        recordMemoirVoiceSearchQuery: () => recording.promise,
        cancelMemoirVoiceSearchRecording: () => cancels.push("cancel"),
      },
      voiceApiExports: {
        parseMemoirVoiceSearchQuery: async (query) => {
          parseCalls.push(query);
          throw new Error("late search must not be parsed");
        },
      },
    });
    const context = pageContext(page);

    const search = page.startVoiceSearch.call(context);
    assert.equal(context.data.voiceSearchRecording, true);
    page[lifecycle].call(context);
    recording.resolve("外婆家的院子");
    await search;

    assert.deepEqual(cancels, ["cancel"]);
    assert.deepEqual(parseCalls, []);
    assert.equal(context.data.voiceSearchRecording, false);
    assert.equal(context.data.voiceSearchTranscribing, false);
    assert.equal(context.data.error, "");
  }
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
      getMemoirVoiceTranscript: async () => ({ ...sampleTranscript("utterance-target"), transcription_status: "ready" }),
      listMemoirVoiceTranscripts: async ({ offset, includeEmpty }) => {
        assert.equal(includeEmpty, false);
        calls.push(offset);
        return {
          items: offset === 0
            ? [sampleTranscript("utterance-old")]
            : [sampleTranscript("utterance-target")],
          total: 2,
          empty_count: 3,
          limit: 1,
          offset,
        };
      },
    },
  });
  const context = pageContext(page, { limit: 1 });

  await page.onShow.call(context);

  assert.deepEqual(calls, [0, 1]);
  assert.equal(context.data.showEmptyRecordings, false);
  assert.equal(context.data.highlightedId, "utterance-target");
  assert.deepEqual(removedKeys, ["pawtale_transcript_navigation_target"]);
  assert.deepEqual(scrolls, [
    {
      selector: "#transcript-utterance-target",
      offsetTop: 16,
      duration: 240,
    },
  ]);
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


for (const status of ["unrecognized", "empty"]) {
test(`no-text recovery keeps original audio recoverable: ${status}`, async () => {
  const item = { ...sampleTranscript("quiet"), transcript_raw: "", transcript_edited: "", display_text: "",
    transcription_status: status, can_recover_transcription: true, display_char_count: 0 };
  const { page } = loadTranscriptsPage({
    authExports: { getMemoirIdentity: async () => ({id: "user-1", active_project_id: "project-1", projects: []}) },
    voiceApiExports: { listMemoirVoiceTranscripts: async () => ({items: [item], total: 1, limit: 30, offset: 0}),
      recoverMemoirVoiceTranscript: async () => ({...item, transcription_status: "unrecognized"}) },
  });
  const context = pageContext(page);
  const toasts = [];
  global.wx = {showToast: value => toasts.push(value)};
  await page.loadTranscripts.call(context, true);
  await page.recoverTranscript.call(context, {currentTarget: {dataset: {id: "quiet"}}});
  assert.equal(context.data.items[0].transcriptionStatus, "unrecognized");
  assert.equal(context.data.items[0].canRecoverTranscription, true);
  assert.equal(toasts.at(-1).icon, "none");
  delete global.wx;
});
}


test("empty recordings fold before pagination and can be expanded, recovered and folded again", async () => {
  const story = sampleTranscript("story");
  const quiet = { ...sampleTranscript("quiet"), transcript_raw: "", display_text: "", transcription_status: "empty",
    summary_title: "未检测到清晰说话声", summary_text: "原录音已保留，可以试听或重新识别。", can_recover_transcription: true };
  let recovered = false;
  const requests = [];
  const { page } = loadTranscriptsPage({
    authExports: { getMemoirIdentity: async () => ({ active_project_id: "project-1", projects: [] }) },
    voiceApiExports: {
      listMemoirVoiceTranscripts: async options => {
        requests.push(options);
        const items = options.includeEmpty || recovered ? [recovered ? story : quiet, story] : [story];
        return { items, total: items.length, empty_count: recovered ? 0 : 1, offset: 0, limit: 30 };
      },
      recoverMemoirVoiceTranscript: async () => { recovered = true; return {...story, id: "quiet", transcription_status: "ready", can_recover_transcription: false}; },
    },
  });
  const context = pageContext(page);
  global.wx = {showToast: () => {}};
  try {
    await context.loadTranscripts(true);
    assert.equal(requests[0].includeEmpty, false);
    assert.equal(context.data.emptyCount, 1);
    assert.deepEqual(context.data.items.map(item => item.id), ["story"]);
    await context.toggleEmptyRecordings();
    assert.equal(requests.at(-1).offset, 0);
    assert.equal(context.data.items[0].id, "quiet");
    assert.equal(context.data.items[0].summaryTitleText, quiet.summary_title);
    await context.recoverTranscript({currentTarget: {dataset: {id: "quiet"}}});
    assert.equal(context.data.emptyCount, 0);
    assert.equal(context.data.items[0].transcriptionStatus, "ready");
    await context.toggleEmptyRecordings();
    assert.equal(requests.at(-1).includeEmpty, false);
  } finally { delete global.wx; }
});


for (const scenario of ["empty", "deleted", "server-error", "other-project", "video", "search-without-empty", "organized-tab", "project-changed"]) {
  test(`navigation reveals only a confirmed empty target: ${scenario}`, async () => {
    const targetId = "navigation-target";
    global.wx = {
      getStorageSync: key => key === "pawtale_transcript_navigation_target" ? { utterance_id: targetId } : "",
      removeStorageSync() {}, pageScrollTo() {},
    };
    const calls = [];
    let detailCalls = 0;
    const noEmpty = scenario === "search-without-empty" || scenario === "organized-tab";
    const { page } = loadTranscriptsPage({
      authExports: { getMemoirIdentity: async () => ({ active_project_id: "project-1", active_project: { title: "往事" } }) },
      voiceApiExports: {
        listMemoirVoiceTranscripts: async options => {
          calls.push(options);
          const items = options.query || options.organized ? [] : noEmpty ? [sampleTranscript(targetId)]
            : options.includeEmpty ? [sampleTranscript("ordinary"), { ...sampleTranscript(targetId), transcription_status: "empty" }]
            : [sampleTranscript("ordinary")];
          return { items, total: items.length, empty_count: noEmpty ? 0 : 1, limit: 30, offset: 0 };
        },
        getMemoirVoiceTranscript: async () => {
          detailCalls++;
          const { ApiError } = require("../services/request.ts");
          if (scenario === "deleted") throw new ApiError(404, "讲述不存在", {});
          if (scenario === "server-error") throw new ApiError(503, "暂时无法连接", {});
          if (scenario === "project-changed") { context.data.activeProjectId = "project-2"; context.loadToken++; }
          return { ...sampleTranscript(targetId), transcription_status: "empty",
            project_id: scenario === "other-project" ? "project-2" : "project-1" };
        },
      },
    });
    const context = pageContext(page, { activeProjectId: "project-1", pickForVideo: scenario === "video",
      searchQuery: scenario === "search-without-empty" ? "搜索词" : "",
      materialTab: scenario === "organized-tab" ? "organized" : "all" });
    await page.onShow.call(context);
    const expanded = scenario === "empty";
    assert.equal(context.data.showEmptyRecordings, expanded);
    assert.equal(calls.some(call => call.includeEmpty), expanded);
    assert.equal(detailCalls, noEmpty || scenario === "video" ? 0 : 1);
    assert.equal(context.data.highlightedId, expanded || noEmpty ? targetId : "");
    if (noEmpty) assert.equal(context.data.hasSearchCriteria, false);
    if (scenario === "organized-tab") {
      assert.deepEqual(calls.map((call) => call.organized), [true, undefined]);
      assert.equal(context.data.materialTab, "all");
    }
    if (scenario === "deleted") assert.equal(context.data.error, "");
    if (scenario === "server-error") assert.equal(context.data.error, "暂时无法连接");
  });
}


for (const targetPosition of [20, 500]) {
  test(`large transcript navigation selects scope before pagination: ${targetPosition}`, async () => {
    global.wx = {
      getStorageSync: key => key === "pawtale_transcript_navigation_target" ? { utterance_id: "empty-target" } : "",
      removeStorageSync() {}, pageScrollTo() {},
    };
    const ordinary = Array.from({ length: 500 }, (_, i) => sampleTranscript(`ordinary-${i}`));
    const target = { ...sampleTranscript("empty-target"), transcription_status: "empty" };
    const expanded = [...ordinary.slice(0, targetPosition), target, ...ordinary.slice(targetPosition)];
    const requests = [];
    const { page } = loadTranscriptsPage({
      authExports: { getMemoirIdentity: async () => ({ active_project_id: "project-1", active_project: { title: "往事" } }) },
      voiceApiExports: {
        listMemoirVoiceTranscripts: async ({ offset, limit, includeEmpty }) => {
          requests.push({ kind: includeEmpty ? "expanded" : "folded", offset, limit });
          const list = includeEmpty ? expanded : ordinary;
          return { items: list.slice(offset, offset + limit), total: list.length, empty_count: 1, offset, limit };
        },
        getMemoirVoiceTranscript: async () => { requests.push({ kind: "detail" }); return target; },
      },
    });
    const context = pageContext(page, { limit: 50 });
    await page.onShow.call(context);
    assert.equal(context.data.highlightedId, target.id);
    assert.equal(context.data.showEmptyRecordings, true);
    assert.deepEqual(requests.filter(r => r.kind === "folded"), [{ kind: "folded", offset: 0, limit: 50 }]);
    assert.equal(requests[1].kind, "detail");
    assert.equal(requests.length, 2 + Math.ceil((targetPosition + 1) / 50));
  });
}

test("voice search sends product modes and shows unified mode chips", async () => {
  for (const [narration_mode, label] of [["ai_interview", "AI 访谈"], ["human_interview", "真人访谈"], ["book_completion", "书稿补充"]]) {
    const calls = [];
    const { page } = loadTranscriptsPage({
      authExports: { getMemoirIdentity: async () => ({ id: "user-1", active_project_id: "project-1", active_project: { id: "project-1", title: "回忆录" }, projects: [] }) },
      voiceSearchExports: { recordMemoirVoiceSearchQuery: async (options) => { options.onRecordingEnd(); return `帮我找${label}记录`; } },
      voiceApiExports: {
        parseMemoirVoiceSearchQuery: async () => ({ query: "", keywords: [], filters: { included: "all", narration_mode }, display_text: label }),
        listMemoirVoiceTranscripts: async (options) => { calls.push(options); return { items: [], total: 0, limit: 30, offset: 0 }; },
      },
    });
    const context = pageContext(page, { activeProjectId: "project-1" });
    await page.startVoiceSearch.call(context);
    assert.equal(calls[0].query, undefined);
    assert.equal(context.data.searchQuery, "");
    assert.equal(calls[0].narrationMode, narration_mode);
    assert.equal(calls[0].guidanceMode, undefined);
    assert.deepEqual(context.data.searchChips, [{ text: label }]);
    assert.equal(context.data.hasSearchCriteria, true);
  }
});
