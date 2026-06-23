require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

function loadTranscriptsPage({ authExports = {}, voiceApiExports = {}, voiceSearchExports = {} } = {}) {
  const authPath = require.resolve("../services/auth.ts");
  const voiceApiPath = require.resolve("../services/memoir-voice-api.ts");
  const voiceSearchPath = require.resolve("../services/memoir-voice-search.ts");
  const transcriptsPath = require.resolve("../pages/transcripts/index.ts");
  delete require.cache[authPath];
  delete require.cache[voiceApiPath];
  delete require.cache[voiceSearchPath];
  delete require.cache[transcriptsPath];
  require.cache[authPath] = {
    id: authPath,
    filename: authPath,
    loaded: true,
    exports: {
      getMemoirIdentity: async () => {
        throw new Error("getMemoirIdentity was not mocked");
      },
      ...authExports,
    },
  };
  require.cache[voiceApiPath] = {
    id: voiceApiPath,
    filename: voiceApiPath,
    loaded: true,
    exports: {
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
      updateMemoirVoiceTranscript: async () => {
        throw new Error("updateMemoirVoiceTranscript was not mocked");
      },
      updateMemoirBookPlan: async () => {
        throw new Error("updateMemoirBookPlan was not mocked");
      },
      ...voiceApiExports,
    },
  };
  require.cache[voiceSearchPath] = {
    id: voiceSearchPath,
    filename: voiceSearchPath,
    loaded: true,
    exports: {
      recordMemoirVoiceSearchQuery: async () => {
        throw new Error("recordMemoirVoiceSearchQuery was not mocked");
      },
      stopMemoirVoiceSearchRecording: () => {},
      ...voiceSearchExports,
    },
  };

  let page = null;
  const previousPage = global.Page;
  global.Page = (definition) => {
    page = definition;
  };
  const mod = require(transcriptsPath);
  if (previousPage === undefined) {
    delete global.Page;
  } else {
    global.Page = previousPage;
  }
  return { mod, page };
}

function pageContext(page, dataPatch = {}) {
  return {
    ...page,
    data: { ...page.data, ...dataPatch },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };
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
        summary: "整理春节团聚。",
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
        summary: "整理童年朋友。",
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

test("transcript book inclusion text shows included chapter titles", () => {
  const { transcriptBookInclusionText } = loadTranscriptsPage().mod;

  assert.equal(
    transcriptBookInclusionText({
      included_chapters: [
        { chapter_id: "chapter-2", title: "离家求学", sort_order: 1 },
        { chapter_id: "chapter-1", title: "童年院子", sort_order: 0 },
      ],
    }),
    "已收录：童年院子、离家求学",
  );
  assert.equal(transcriptBookInclusionText({ included_chapters: [] }), "未收录到章节");
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
  assert.equal(item.rawCollapsible, true);
  assert.equal(item.displayCollapsible, true);
  assert.equal(item.rawExpanded, false);
  assert.equal(item.displayExpanded, false);
  assert.equal(item.rawSegments.map((segment) => segment.text).join("").endsWith("..."), true);
  assert.equal(item.displaySegments.map((segment) => segment.text).join("").length, 183);

  page.toggleTranscriptText.call(context, {
    currentTarget: { dataset: { id: "utterance-long", field: "display" } },
  });

  item = context.data.items[0];
  assert.equal(item.displayExpanded, true);
  assert.equal(item.displayToggleText, "收起");
  assert.equal(item.displaySegments.map((segment) => segment.text).join(""), longDisplay);
});

test("transcripts page formats record time like book uncovered sources", async () => {
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

  assert.equal(context.data.items[0].createdAtText, "06-17 08:00 北京时间");
});

test("transcripts page moves an included source to another chapter", async () => {
  const updates = [];
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
      updateMemoirBookPlan: async (projectId, data) => {
        updates.push({ projectId, data });
        return {
          ...sampleBookPlan(),
          chapters: data.chapters.map((chapter, index) => ({
            ...sampleBookPlan().chapters[index],
            ...chapter,
            sort_order: index,
            source_refs: chapter.source_refs,
          })),
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

  assert.equal(updates.length, 1);
  assert.deepEqual(
    updates[0].data.chapters[0].source_refs.map((ref) => ref.utterance_id),
    ["utterance-1"],
  );
  assert.deepEqual(
    updates[0].data.chapters[1].source_refs.map((ref) => ref.utterance_id),
    ["utterance-3", "utterance-2"],
  );
  assert.deepEqual(toasts, [{ title: "来源已移动", icon: "success" }]);
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
