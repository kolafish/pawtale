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
    listMemoirVoiceTranscripts: async () => {
      throw new Error("listMemoirVoiceTranscripts was not mocked");
    },
    parseMemoirVoiceSearchQuery: async () => {
      throw new Error("parseMemoirVoiceSearchQuery was not mocked");
    },
    recoverMemoirVoiceTranscript: async () => {
      throw new Error("recoverMemoirVoiceTranscript was not mocked");
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
  assert.equal(context.data.items[0].bookInclusionText, "尚未入章");
  assert.equal(context.data.pageMetaText, "我的回忆录 · 1 条讲述");
  assert.equal(context.data.showNoProjectPanel, false);
  assert.equal(context.data.showGenericEmpty, false);
  assert.equal(context.data.showLoadMore, false);
  assert.equal(context.data.items[0].cardClass, "transcript-card");
  assert.equal(context.data.items[0].bookStatusClass, "book-status");
  assert.equal(context.data.items[0].detailActionText, "查看详情");
  assert.match(context.data.items[0].cardAriaLabel, /爷爷退休后仍帮公司做事/);
  assert.equal("transcript_raw" in context.data.items[0], false);
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
  assert.equal(item.summaryTitleText, "音频已保存，等待转写");
  assert.equal(item.summaryPreviewText, "这段录音已经保存，但还没有识别成文字。");
  assert.equal(item.recordMetaText, "07-01 15:38 · 8 秒");
  assert.equal(item.recoveryNoticeText, "音频已保存，恢复转写后即可继续整理。");
  assert.equal(item.bookInclusionText, "等待转写后再入章");
  assert.equal(item.bookStatusClass, "book-status pending");
  assert.equal(item.cardClass, "transcript-card recovery-pending");
  assert.equal(item.detailActionText, "查看音频");
  assert.match(item.cardAriaLabel, /查看音频$/);
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
  assert.deepEqual(toasts, [{ title: "已恢复转写", icon: "success" }]);
  delete global.wx;
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

  delete global.wx;
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
