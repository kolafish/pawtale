require("./register-ts");

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const {
  mockMemoirApiModules,
  flushAsyncWork,
  loadPageModule,
  mockModule,
  pageContext,
  unmockedAsync,
} = require("./page-harness");

const {
  sampleAudioResponse,
  sampleAutomationEvent,
  sampleBookManuscript,
  sampleBookOutput,
  sampleDraft,
  samplePlan,
  sampleRef,
  sampleStructuredBookContent,
} = require("./book-fixtures");

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function planFromStructureCommand(status, command) {
  const base = samplePlan(status);
  if (command.type === "delete_chapter") {
    return {
      ...base,
      chapters: base.chapters
        .filter((chapter) => chapter.id !== command.chapter_id)
        .map((chapter, index) => ({ ...chapter, sort_order: index })),
    };
  }
  if (command.type === "set_chapter_book_inclusion") {
    return {
      ...base,
      chapters: base.chapters.map((chapter) => (
        chapter.id === command.chapter_id
          ? { ...chapter, include_in_book: command.include_in_book }
          : chapter
      )),
    };
  }
  if (command.type === "move_chapter") {
    const chapters = [...base.chapters];
    const fromIndex = chapters.findIndex((chapter) => chapter.id === command.chapter_id);
    const moving = chapters[fromIndex];
    if (command.direction) {
      const toIndex = command.direction === "up" ? fromIndex - 1 : fromIndex + 1;
      [chapters[fromIndex], chapters[toIndex]] = [chapters[toIndex], chapters[fromIndex]];
      return { ...base, chapters: chapters.map((chapter, index) => ({ ...chapter, sort_order: index })) };
    }
    const remaining = chapters.filter((chapter) => chapter.id !== command.chapter_id);
    let toIndex = remaining.length;
    if (command.target_position === "first") {
      toIndex = 0;
    } else if (typeof command.target_index === "number") {
      toIndex = Math.min(command.target_index - 1, remaining.length);
    } else if (command.relative_chapter_id) {
      const relativeIndex = remaining.findIndex((chapter) => chapter.id === command.relative_chapter_id);
      toIndex = relativeIndex + (command.relative_position === "after" ? 1 : 0);
    }
    remaining.splice(toIndex, 0, moving);
    return { ...base, chapters: remaining.map((chapter, index) => ({ ...chapter, sort_order: index })) };
  }
  if (command.type === "merge_chapters") {
    const source = base.chapters.find((chapter) => chapter.id === command.chapter_id);
    return {
      ...base,
      chapters: base.chapters
        .filter((chapter) => chapter.id !== command.chapter_id)
        .map((chapter, index) => (
          chapter.id === command.target_chapter_id && source
            ? {
              ...chapter,
              sort_order: index,
              source_refs: [...chapter.source_refs, ...source.source_refs],
            }
            : { ...chapter, sort_order: index }
        )),
    };
  }
  if (command.type === "split_chapter") {
    const source = base.chapters.find((chapter) => chapter.id === command.chapter_id);
    if (!source) {
      return base;
    }
    const selected = new Set(command.utterance_ids || []);
    const keptRefs = source.source_refs.filter((ref) => !selected.has(ref.utterance_id));
    const splitRefs = source.source_refs.filter((ref) => selected.has(ref.utterance_id));
    return {
      ...base,
      chapters: [
        { ...source, source_refs: keptRefs, sort_order: 0 },
        {
          id: "chapter-3",
          plan_id: "plan-1",
          project_id: "project/1",
          title: command.title,
          sort_order: 1,
          source_refs: splitRefs,
          metadata_json: {},
          created_at: "2026-06-17T00:00:00.000Z",
          updated_at: "2026-06-17T00:00:00.000Z",
        },
        { ...base.chapters[1], sort_order: 2 },
      ],
    };
  }
  if (command.type === "rename_chapter") {
    return {
      ...base,
      chapters: base.chapters.map((chapter) => (
        chapter.id === command.chapter_id
          ? { ...chapter, title: command.new_title || command.title }
          : chapter
      )),
    };
  }
  return base;
}

function loadBookPage({
  authExports = {},
  voiceApiExports = {},
  ttsExports = {},
  cacheExports = {},
} = {}) {
  const getMemoirIdentity = authExports.getMemoirIdentity || unmockedAsync("getMemoirIdentity");
  mockModule("../services/auth.ts", {
    getMemoirIdentity,
    ...authExports,
  });
  mockMemoirApiModules({
    applyMemoirBookPlanStructureCommand: unmockedAsync("applyMemoirBookPlanStructureCommand"),
    confirmMemoirBookPlan: unmockedAsync("confirmMemoirBookPlan"),
    createMemoirBookChapterDraftTTS: unmockedAsync("createMemoirBookChapterDraftTTS"),
    createMemoirBookPlanOutlineTTS: unmockedAsync("createMemoirBookPlanOutlineTTS"),
    assembleMemoirBookOutput: unmockedAsync("assembleMemoirBookOutput"),
    updateMemoirBookOutput: unmockedAsync("updateMemoirBookOutput"),
    downloadMemoirVoiceAudio: unmockedAsync("downloadMemoirVoiceAudio"),
    generateMemoirBookChapterTitle: unmockedAsync("generateMemoirBookChapterTitle"),
    updateMemoirBookChapterTitle: unmockedAsync("updateMemoirBookChapterTitle"),
    generateMemoirBookPlan: unmockedAsync("generateMemoirBookPlan"),
    getMemoirBookChapterDraft: async () => null,
    getMemoirBookOutput: async () => null,
    getMemoirBookShareStatus: unmockedAsync("getMemoirBookShareStatus"),
    createMemoirBookShare: unmockedAsync("createMemoirBookShare"),
    revokeMemoirBookShare: unmockedAsync("revokeMemoirBookShare"),
    getMemoirBookSnapshot: async () => {
      const identity = await getMemoirIdentity();
      return {
        server_revision: 1,
        identity,
        plan: null,
        drafts_by_chapter_id: {},
        output: null,
        automation_events: { items: [], total: 0, limit: 8 },
        image_assets: {
          items: [],
          total: 0,
          ready_count: 0,
          included_count: 0,
          unused_count: 0,
        },
      };
    },
    getMemoirBookPlan: unmockedAsync("getMemoirBookPlan"),
    listMemoirAutomationEvents: async () => ({ items: [], total: 0, limit: 8 }),
    acknowledgeMemoirAutomationEvent: async () => ({}),
    acknowledgeDismissibleMemoirAutomationEvents: async () => ({ updated_count: 0 }),
    ...voiceApiExports,
  });
  mockModule("../stores/memoir-read-cache.ts", {
    readCachedMemoirBook: () => ({ status: "miss" }),
    writeCachedMemoirBook: () => {},
    ...cacheExports,
  });
  mockModule("../services/tts-settings.ts", {
    readBookTTSRequest: () => ({}),
    ...ttsExports,
  });

  return loadPageModule("../pages/book/index.ts");
}

test("book page summarizes chapter source count without exposing raw ids", () => {
  const { mod } = loadBookPage();

  assert.equal(
    mod.bookChapterSourceSummary({
      source_refs: [
        sampleRef("utterance-1", "外婆家的院子"),
        sampleRef("utterance-2", "第一次离家"),
        sampleRef("utterance-3", "第三段"),
      ],
    }),
    "本章素材：3 条原始讲述",
  );
});

test("book page keeps one export and settings entry", () => {
  const pageWxml = fs.readFileSync(path.join(__dirname, "../pages/book/index.wxml"), "utf8");
  const { page } = loadBookPage();

  assert.equal((pageWxml.match(/bindtap="openBookExportPage"/g) || []).length, 1);
  assert.match(pageWxml, /导出与成书设置/);
  assert.deepEqual(page.onShareAppMessage.call(pageContext(page)), {
    title: "老余口述",
    path: "/pages/home/index",
  });
  assert.equal(page.onShareTimeline, undefined);
});

test("book page shares only an explicitly created family link and revokes it", async () => {
  const revoked = [];
  const { page } = loadBookPage({
    voiceApiExports: {
      getMemoirBookShareStatus: async () => ({ active: false, expires_at: null }),
      createMemoirBookShare: async () => ({ token: "family-secret", expires_at: "2026-09-28T00:00:00" }),
      revokeMemoirBookShare: async (projectId) => { revoked.push(projectId); },
    },
  });
  const context = pageContext(page, {
    projectId: "project/1",
    bookContentAvailable: true,
    bookPreviewTitle: "外婆回忆录",
  });

  await page.openBookSharePanel.call(context);
  assert.equal(context.data.bookShareActive, false);
  assert.equal(context.data.bookShareReady, false);
  await page.createBookShare.call(context);
  assert.equal(context.data.bookShareReady, true);
  assert.deepEqual(page.onShareAppMessage.call(context), {
    title: "阅读《外婆回忆录》",
    path: "/pages/book-reader/index?shareToken=family-secret",
  });
  global.wx = { showModal: ({ success }) => success({ confirm: true }) };
  await page.revokeBookShare.call(context);
  assert.deepEqual(revoked, ["project/1"]);
  assert.equal(context.data.bookShareReady, false);
  assert.equal(page.onShareAppMessage.call(context).path, "/pages/home/index");
});

test("book page restores an existing family link for sharing without regenerating it", async () => {
  let createCount = 0;
  const { page } = loadBookPage({
    voiceApiExports: {
      getMemoirBookShareStatus: async () => ({
        active: true,
        token: "v1.existing-share",
        expires_at: "2026-09-28T00:00:00",
      }),
      createMemoirBookShare: async () => { createCount += 1; throw new Error("unexpected regeneration"); },
    },
  });
  const context = pageContext(page, {
    projectId: "project/1",
    bookContentAvailable: true,
    bookPreviewTitle: "外婆回忆录",
  });

  await page.openBookSharePanel.call(context);
  assert.equal(context.data.bookShareActive, true);
  assert.equal(context.data.bookShareReady, true);
  assert.deepEqual(page.onShareAppMessage.call(context), {
    title: "阅读《外婆回忆录》",
    path: "/pages/book-reader/index?shareToken=v1.existing-share",
  });
  assert.equal(createCount, 0);
});

test("book page never shares a previous project's link after the active project changes", async () => {
  let resolveStatus;
  const { page } = loadBookPage({
    voiceApiExports: {
      getMemoirBookShareStatus: () => new Promise((resolve) => { resolveStatus = resolve; }),
    },
  });
  const context = pageContext(page, { projectId: "first-project", bookContentAvailable: true });

  const opening = page.openBookSharePanel.call(context);
  context.setData({ projectId: "second-project" });
  resolveStatus({ active: true, token: "first-project-link", expires_at: "2026-09-28T00:00:00" });
  await opening;

  assert.equal(context.data.bookShareReady, false);
  assert.equal(page.onShareAppMessage.call(context).path, "/pages/home/index");
});

test("book page asks before replacing an existing family link", async () => {
  let createCount = 0;
  let confirm = false;
  global.wx = { showModal: ({ success }) => success({ confirm }) };
  const { page } = loadBookPage({
    voiceApiExports: {
      createMemoirBookShare: async () => {
        createCount += 1;
        return { token: "next-link", expires_at: "2026-09-29T00:00:00" };
      },
    },
  });
  const context = pageContext(page, {
    projectId: "project/1",
    bookShareActive: true,
    bookShareStatusKnown: true,
  });

  await page.regenerateBookShare.call(context);
  assert.equal(createCount, 0);
  confirm = true;
  await page.regenerateBookShare.call(context);
  assert.equal(createCount, 1);
  assert.equal(context.bookShareToken, "next-link");
});

test("book page routes chapter card actions to the chapter workspace", () => {
  const navigations = [];
  global.wx = {
    navigateTo(options) {
      navigations.push(options.url);
    },
  };
  const { mod, page } = loadBookPage();
  const context = pageContext(page, { projectId: "project/1" });

  assert.equal(
    mod.bookChapterDetailUrl("project/1", "chapter/1"),
    "/pages/chapter-workspace/index?projectId=project%2F1&chapterId=chapter%2F1",
  );

  page.openChapterEditor.call(context, { currentTarget: { dataset: { id: "chapter/1" } } });

  assert.deepEqual(navigations, [
    "/pages/chapter-workspace/index?projectId=project%2F1&chapterId=chapter%2F1",
  ]);
});

test("book page routes unfinished book progress into the unified completion flow", () => {
  const navigations = [];
  global.wx = {
    navigateTo(options) {
      navigations.push(options.url);
    },
  };
  const { mod, page } = loadBookPage();
  const context = pageContext(page, {
    projectId: "project/1",
    projectTitle: "爷爷的故事",
    stage: "drafting",
  });
  context.stopPlayback = () => {};

  assert.equal(
    mod.bookCompletionPageUrl("project/1", "爷爷的故事"),
    "/pages/book-completion/index?projectId=project%2F1&projectTitle=%E7%88%B7%E7%88%B7%E7%9A%84%E6%95%85%E4%BA%8B",
  );

  page.openBookCompletion.call(context);

  assert.deepEqual(navigations, [
    "/pages/book-completion/index?projectId=project%2F1&projectTitle=%E7%88%B7%E7%88%B7%E7%9A%84%E6%95%85%E4%BA%8B",
  ]);
});

test("book page exposes chapter reordering after the plan is confirmed", () => {
  global.wx = { switchTab() {} };
  const { page } = loadBookPage();
  const context = pageContext(page, { projectId: "project/1" });
  page.applyPlan.call(context, samplePlan("confirmed"));

  assert.equal(context.data.stage, "drafting");
  assert.equal(context.data.chapterStructureEditing, false);

  page.toggleChapterStructureEditing.call(context);
  assert.equal(context.data.chapterStructureEditing, true);

  page.toggleChapterStructureEditing.call(context);
  assert.equal(context.data.chapterStructureEditing, false);
});

test("book page opens secondary chapter actions from the more sheet", () => {
  const navigations = [];
  const actionSheets = [];
  const audioCalls = [];
  const tapIndexes = [2, 99, 99];
  global.wx = {
    navigateTo(options) {
      navigations.push(options.url);
    },
    showActionSheet(options) {
      actionSheets.push(options.itemList);
      const tapIndex = tapIndexes[actionSheets.length - 1];
      if (tapIndex < options.itemList.length) {
        options.success({ tapIndex });
      }
    },
    showToast() {},
  };
  const { mod, page } = loadBookPage();
  const chapters = mod.buildChapterViews(samplePlan("confirmed"), {
    "chapter-1": sampleDraft(1, "draft"),
  });
  const context = pageContext(page, { projectId: "project/1", chapters });
  context.playChapterAudioById = (chapterId) => {
    audioCalls.push(chapterId);
  };

  page.openChapterMoreActions.call(context, { currentTarget: { dataset: { id: "chapter-1" } } });
  page.openChapterMoreActions.call(context, { currentTarget: { dataset: { id: "chapter-1" } } });
  context.data.ttsBusyTarget = "chapter:chapter-2";
  page.openChapterMoreActions.call(context, { currentTarget: { dataset: { id: "chapter-1" } } });

  assert.deepEqual(actionSheets, [
    ["修改章名", "AI 重新起名", "朗读", "不收入本书"],
    ["修改章名", "AI 重新起名", "朗读", "不收入本书"],
    ["修改章名", "AI 重新起名", "不收入本书"],
  ]);
  assert.deepEqual(navigations, []);
  assert.deepEqual(audioCalls, ["chapter-1"]);
});

test("book page hides and restores a chapter without deleting its content", async () => {
  const calls = [];
  const actionSheets = [];
  global.wx = {
    navigateTo() {},
    showActionSheet(options) {
      actionSheets.push(options.itemList);
      options.success({ tapIndex: options.itemList.length - 1 });
    },
    showToast() {},
    switchTab() {},
  };
  const { page } = loadBookPage({
    voiceApiExports: {
      applyMemoirBookPlanStructureCommand: async (projectId, data) => {
        calls.push({ projectId, data });
        return planFromStructureCommand("confirmed", data);
      },
      getMemoirBookChapterDraft: async () => null,
      getMemoirBookOutput: async () => null,
    },
  });
  const context = pageContext(page, { projectId: "project/1", projectTitle: "外婆回忆录" });
  page.applyPlan.call(context, samplePlan("confirmed"));

  page.openChapterMoreActions.call(context, {
    currentTarget: { dataset: { id: "chapter-2" } },
  });
  await flushAsyncWork();

  assert.deepEqual(actionSheets[0], ["修改章名", "AI 重新起名", "不收入本书"]);
  assert.deepEqual(calls[0], {
    projectId: "project/1",
    data: {
      type: "set_chapter_book_inclusion",
      chapter_id: "chapter-2",
      include_in_book: false,
    },
  });
  assert.equal(context.data.chapters[1].include_in_book, false);
  assert.match(context.data.chapters[1].chapterSummaryMetaText, /^不收入本书/);
  assert.equal(context.data.totalChapterCount, 1);

  page.openChapterMoreActions.call(context, {
    currentTarget: { dataset: { id: "chapter-2" } },
  });
  await flushAsyncWork();

  assert.deepEqual(actionSheets[1], ["修改章名", "AI 重新起名", "恢复收入本书"]);
  assert.equal(calls[1].data.include_in_book, true);
  assert.equal(context.data.chapters[1].include_in_book, true);
  assert.equal(context.data.totalChapterCount, 2);
});

test("book page routes reading and export actions to their focused pages", () => {
  const navigations = [];
  global.wx = {
    navigateTo(options) {
      navigations.push(options.url);
    },
  };
  const { mod, page } = loadBookPage();
  const context = pageContext(page, {
    projectId: "project/1",
    bookContentAvailable: true,
  });
  context.stopPlayback = () => {};

  assert.equal(
    mod.bookExportPageUrl("project/1", "print_pdf"),
    "/pages/book-export/index?projectId=project%2F1&action=print_pdf",
  );
  assert.equal(
    mod.bookReaderPageUrl("project/1"),
    "/pages/book-reader/index?projectId=project%2F1",
  );

  page.openBookReader.call(context);
  page.openBookExportPage.call(context, { currentTarget: { dataset: { action: "preview" } } });

  assert.deepEqual(navigations, [
    "/pages/book-reader/index?projectId=project%2F1",
    "/pages/book-export/index?projectId=project%2F1&action=preview",
  ]);
});

test("book page switches between chapter work and secondary tools", () => {
  const { writeBookWorkspaceNavigationTarget } = require("../services/book-workspace-navigation.ts");
  const { page } = loadBookPage();
  const context = pageContext(page, { bookWorkspaceView: "chapters" });

  writeBookWorkspaceNavigationTarget("tools");
  page.applyBookWorkspaceNavigationTarget.call(context);
  assert.equal(context.data.bookWorkspaceView, "tools");

  page.changeBookWorkspaceView.call(context, {
    currentTarget: { dataset: { view: "tools" } },
  });
  assert.equal(context.data.bookWorkspaceView, "tools");

  page.changeBookWorkspaceView.call(context, {
    currentTarget: { dataset: { view: "unsupported" } },
  });
  assert.equal(context.data.bookWorkspaceView, "tools");
});

test("book page builds outline narration for saved chapter titles", () => {
  const { mod } = loadBookPage();
  const plan = samplePlan("confirmed");
  const chapters = mod.buildChapterViews(plan);
  chapters[0].editingTitle = "未保存的新标题";

  assert.equal(
    mod.bookOutlineNarrationText(plan.title, chapters),
    [
      "《外婆回忆录章节规划》现在有 2 章。",
      "第 1 章，外婆家的童年。本章收录 1 条语音素材。",
      "第 2 章，第一次离家。本章收录 1 条语音素材。",
      "你可以继续调整章节顺序、合并章节，或者说出想修改的地方。",
    ].join("\n"),
  );
});

test("book page counts current readable chapters without a confirmation gate", () => {
  const { mod } = loadBookPage();
  const plan = samplePlan("confirmed");
  const chapters = mod.buildChapterViews(plan, {
    "chapter-1": sampleDraft(1, "confirmed"),
    "chapter-2": sampleDraft(1, "draft"),
  });

  assert.equal(mod.bookProgressText(chapters), "2/2 章正文已更新，2 章已有正文");
  assert.equal(chapters[0].chapterNeedsAction, false);
  assert.equal(chapters[0].chapterGuidanceText, "正文已更新，可以阅读或修改。");
  assert.equal(chapters[0].draftActionText, "阅读或修改");
  assert.equal(chapters[1].chapterNeedsAction, false);
  assert.equal(chapters[1].chapterGuidanceText, "正文已更新，可以阅读或修改。");
  assert.equal(chapters[1].draftActionText, "阅读或修改");
  assert.doesNotMatch(chapters[0].chapterSummaryMetaText, /已确认|待确认/);
  assert.equal("draft" in chapters[0], false);
  assert.equal("draftContent" in chapters[0], false);
  assert.equal("source_refs" in chapters[0], false);
  assert.equal("sections" in chapters[0], false);
  assert.equal("metadata_json" in chapters[0], false);
});

test("book page directs chapters without a draft to their existing workspace", () => {
  const { mod } = loadBookPage();
  const [chapter] = mod.buildChapterViews(samplePlan("confirmed"));

  assert.equal(chapter.chapterNeedsAction, true);
  assert.equal(chapter.chapterGuidanceText, "正文还没准备好，进入章节页查看整理进度。");
  assert.equal(chapter.draftActionText, "查看整理进度");
  assert.doesNotMatch(chapter.chapterSummaryMetaText, /未生成|待确认/);

  const planWithNewSource = samplePlan("confirmed");
  planWithNewSource.chapters[0].pending_source_count = 1;
  const [unpreparedChapter] = mod.buildChapterViews(planWithNewSource);
  assert.equal(unpreparedChapter.draftActionText, "查看整理进度");
});

test("book page excludes hidden chapters from full-book generation readiness", () => {
  global.wx = { switchTab() {} };
  const { page } = loadBookPage();
  const plan = samplePlan("confirmed");
  plan.chapters[1].include_in_book = false;
  const context = pageContext(page, { projectId: "project/1" });
  page.applyPlan.call(context, plan, {
    "chapter-1": sampleDraft(1, "confirmed"),
    "chapter-2": sampleDraft(1, "draft"),
  });

  assert.equal(context.data.readyDraftCount, 1);
  assert.equal(context.data.totalChapterCount, 1);
  assert.equal(context.data.progressText, "1/1 章正文已更新，1 章已有正文");
  assert.equal("plan" in context.data, false);
});

test("book page stores only the full-book summary needed by the home view", () => {
  const { page } = loadBookPage();
  const output = sampleBookOutput(
    3,
    "全书正文",
    true,
    ["外婆家的童年", "第一次离家", "县城生活"],
    [],
    sampleBookManuscript(),
  );
  const context = pageContext(page);
  page.applyBookOutput.call(context, output);
  const data = context.data;

  assert.equal(context.bookOutput, output);
  assert.equal(data.bookContentAvailable, true);
  assert.equal(data.bookCharCount, 4);
  assert.equal(data.bookPreviewTitle, "外婆回忆录");
  assert.equal(data.bookStaleNotice, "章节「外婆家的童年、第一次离家」等章节已更新，请重新整理一版。");
  assert.equal("bookPreviewChapters" in data, false);
  assert.equal("bookTitleInput" in data, false);
  assert.equal("bookOutput" in data, false);
  assert.equal("bookContent" in data, false);

  page.applyPlan.call(context, samplePlan("draft"));
  assert.equal(context.bookOutput, null);
  assert.equal(context.data.bookContentAvailable, false);
});

test("book page stays available when login is required", async () => {
  const switches = [];
  global.wx = {
    switchTab(options) {
      switches.push(options.url);
    },
  };
  const { ApiError } = require("../services/request.ts");
  const { page } = loadBookPage({
    authExports: {
      getMemoirIdentity: async () => {
        throw new ApiError(401, "请先登录", {});
      },
    },
  });
  const context = pageContext(page);

  await page.loadBookHome.call(context);

  assert.equal(context.data.needsLogin, true);
  assert.equal(context.data.projectTitle, "我的回忆录");
  assert.equal(context.data.progressText, "登录后可以开始整理");
  assert.deepEqual(switches, []);
});

test("book page labels a cached snapshot after refresh fails and clears it on retry", async () => {
  const snapshot = {
    server_revision: 1,
    identity: {
      active_project_id: "project/1",
      active_project: { title: "外婆回忆录" },
    },
    plan: null,
    drafts_by_chapter_id: {},
    output: sampleBookOutput(1, "上次保存的书稿"),
    automation_events: { items: [], total: 0, limit: 8 },
    image_assets: { items: [], total: 0, ready_count: 0, included_count: 0, unused_count: 0 },
  };
  let fetchCount = 0;
  const { page } = loadBookPage({
    cacheExports: {
      readCachedMemoirBook: () => ({ status: "hit", payload: snapshot, localInvalidationRevision: 0 }),
    },
    voiceApiExports: {
      getMemoirBookSnapshot: async () => {
        fetchCount += 1;
        if (fetchCount === 1) throw new Error("网络不可用");
        return snapshot;
      },
    },
  });
  const context = pageContext(page);
  const oldWarn = console.warn;
  console.warn = () => {};
  try {
    await page.loadBookHome.call(context);
  } finally {
    console.warn = oldWarn;
  }

  assert.equal(context.data.bookContentAvailable, true);
  assert.equal(context.data.projectTitle, "外婆回忆录");
  assert.equal(context.data.showingCachedBook, true);
  assert.equal(context.data.error, "");

  await page.loadBookHome.call(context);

  assert.equal(fetchCount, 2);
  assert.equal(context.data.showingCachedBook, false);
});

test("book page opens the transcript list as a secondary page", () => {
  const navigations = [];
  global.wx = {
    navigateTo(options) {
      navigations.push(options.url);
    },
  };
  const { page } = loadBookPage();
  const context = pageContext(page);

  page.openTranscripts.call(context);

  assert.deepEqual(navigations, ["/pages/transcripts/index"]);
});

test("book page asks logged-in users to create a memoir before loading book content", async () => {
  let planCalls = 0;
  const { page } = loadBookPage({
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
      getMemoirBookPlan: async () => {
        planCalls += 1;
        return samplePlan("confirmed");
      },
    },
  });
  const context = pageContext(page);

  await page.loadBookHome.call(context);

  assert.equal(context.data.needsLogin, false);
  assert.equal(context.data.projectId, "");
  assert.equal(context.data.projectTitle, "尚未创建回忆录");
  assert.equal(context.data.progressText, "还没有章节");
  assert.equal(planCalls, 0);
});

test("book page builds chapter structure controls from source refs", () => {
  const { mod } = loadBookPage();
  const plan = samplePlan("confirmed");
  plan.chapters[0].source_refs = [
    sampleRef("utterance-1", "外婆家的院子"),
    sampleRef("utterance-3", "春节做年糕"),
  ];

  const chapters = mod.buildChapterViews(plan);

  assert.equal(chapters[0].canMoveUp, false);
  assert.equal(chapters[0].canMoveDown, true);
  assert.equal(chapters[1].canMoveUp, true);
  assert.equal(chapters[0].sourceViews.length, 0);
  assert.equal(chapters[0].sourceToggleText, "查看 2 条");
  assert.deepEqual(chapters[0].mergeTargetOptions, [
    { label: "第 2 章 第一次离家", value: "chapter-2" },
  ]);
  assert.equal(chapters[0].splitSelectedCount, 0);
  assert.equal(chapters[0].splitActionText, "拆出所选");
  assert.equal(chapters[0].canSplitSelectedSources, false);
});

test("book page toggles chapter source details", () => {
  const { page } = loadBookPage();
  const context = pageContext(page);
  const plan = samplePlan("confirmed");
  plan.chapters[0].source_refs = [
    sampleRef("utterance-1", "外婆家的院子"),
    sampleRef("utterance-3", "春节做年糕"),
  ];
  page.applyPlan.call(context, plan);

  assert.equal(context.data.chapters[0].sourceListExpanded, false);
  page.toggleChapterSources.call(context, {
    currentTarget: { dataset: { id: "chapter-1" } },
  });

  assert.equal(context.data.chapters[0].sourceListExpanded, true);
  assert.equal(context.data.chapters[0].sourceToggleText, "收起素材");
  assert.equal(context.data.chapters[0].sourceViews.length, 2);
  assert.equal(context.data.chapters[0].sourceViews[0].previewText, "外婆家的院子");
  assert.equal(context.data.chapters[0].sourceViews[0].recordNumber, 1);
  assert.equal(context.data.chapters[0].sourceViews[0].labelText, "语音记录 1 · 素材 1 · 06-17 08:00 · 6 字");
});

test("book page marks assigned sources as supplemental chapter work", () => {
  const { mod } = loadBookPage();
  const plan = samplePlan("confirmed");
  plan.chapters[0].pending_source_count = 1;
  plan.chapters[0].pending_new_source_count = 1;

  const chapters = mod.buildChapterViews(plan, {
    "chapter-1": sampleDraft(1, "draft", "第一版正文"),
  });

  assert.equal(chapters[0].pendingSourceCount, 1);
  assert.match(chapters[0].chapterGuidanceText, /新增讲述尚未写入正文.*当前正文仍可阅读/);
  assert.equal(chapters[0].draftActionText, "查看更新状态");
  assert.equal(chapters[0].chapterNeedsAction, true);
});

test("book page labels completion answers separately from new narration", () => {
  const { mod } = loadBookPage();
  const plan = samplePlan("confirmed");
  plan.chapters[0].pending_source_count = 2;
  plan.chapters[0].pending_completion_source_count = 2;

  const chapters = mod.buildChapterViews(plan, {
    "chapter-1": sampleDraft(1, "confirmed", "第一版正文"),
  });

  assert.equal(chapters[0].pendingCompletionSourceCount, 2);
  assert.equal(chapters[0].pendingNewSourceCount, 0);
  assert.match(chapters[0].chapterGuidanceText, /补充回答尚未写入正文.*当前正文仍可阅读/);
  assert.equal(chapters[0].draftActionText, "查看更新状态");
  assert.equal(chapters[0].chapterNeedsAction, true);
});

test("book page shows the number of visible sections in each chapter", () => {
  const { mod } = loadBookPage();
  const chapters = mod.buildChapterViews(samplePlan("confirmed"), {
    "chapter-1": sampleDraft(1, "confirmed", "第一版正文"),
  });

  assert.equal(chapters[0].sectionCount, 1);
  assert.match(chapters[0].chapterSummaryMetaText, /^1 个小节 · 1 条口述/);
});

test("book page marks deleted chapter sources for review", () => {
  const { mod } = loadBookPage();
  const plan = samplePlan("confirmed");
  plan.chapters[0].source_refs = [];
  plan.chapters[0].removed_source_count = 1;

  const chapters = mod.buildChapterViews(plan, {
    "chapter-1": sampleDraft(1, "draft", "第一版正文"),
  });

  assert.equal(chapters[0].sourceCount, 0);
  assert.equal(chapters[0].removedSourceCount, 1);
  assert.match(chapters[0].chapterGuidanceText, /讲述已删除.*当前正文仍可阅读/);
  assert.equal(chapters[0].draftActionText, "查看更新状态");

  plan.chapters[0].pending_source_count = 1;
  plan.chapters[0].pending_new_source_count = 1;
  const [changedChapter] = mod.buildChapterViews(plan, {
    "chapter-1": sampleDraft(1, "draft", "第一版正文"),
  });
  assert.match(changedChapter.chapterGuidanceText, /1 条讲述已删除，1 条新增讲述尚未写入正文/);
});

test("book page confirms chapters with trimmed user edits and original source refs", async () => {
  const submissions = [];
  const confirmedPlan = samplePlan("confirmed");
  global.wx = { showToast() {} };
  const { mod, page } = loadBookPage({
    voiceApiExports: {
      confirmMemoirBookPlan: async (projectId, data) => {
        submissions.push({ projectId, data });
        return confirmedPlan;
      },
      getMemoirBookChapterDraft: async () => null,
    },
  });
  const plan = samplePlan("draft");
  const chapters = mod.buildChapterViews(plan);
  chapters[0].editingTitle = "  童年院子  ";
  chapters[1].include_in_book = false;
  const context = pageContext(page, {
    projectId: "project/1",
    projectTitle: "外婆回忆录",
    planTitle: "  用户确认目录  ",
    chapters,
  });
  context.bookPlan = plan;
  context.bookChapterSources = Object.fromEntries(
    plan.chapters.map((chapter) => [chapter.id, chapter.source_refs]),
  );

  await page.confirmPlan.call(context);

  assert.equal(submissions.length, 1);
  assert.equal(submissions[0].projectId, "project/1");
  assert.equal(submissions[0].data.title, "用户确认目录");
  assert.equal(submissions[0].data.chapters[0].title, "童年院子");
  assert.equal(submissions[0].data.chapters[0].include_in_book, true);
  assert.equal(submissions[0].data.chapters[1].include_in_book, false);
  assert.equal("summary" in submissions[0].data.chapters[0], false);
  assert.equal(submissions[0].data.chapters[0].source_refs[0].utterance_id, "utterance-1");
  assert.equal(context.data.stage, "drafting");
});

test("book page renames a confirmed chapter manually or through AI", async () => {
  const manualCalls = [];
  const aiCalls = [];
  const actionLabels = ["修改章名", "AI 重新起名"];
  global.wx = {
    showActionSheet(options) {
      options.success({ tapIndex: options.itemList.indexOf(actionLabels.shift()) });
    },
    showModal(options) {
      options.success({ confirm: true, content: "  院子里的童年  " });
    },
    showToast() {},
    switchTab() {},
  };
  const manualPlan = samplePlan("confirmed");
  manualPlan.chapters[0].title = "院子里的童年";
  const aiPlan = samplePlan("confirmed");
  aiPlan.chapters[0].title = "外婆院中的日子";
  const { page } = loadBookPage({
    voiceApiExports: {
      generateMemoirBookChapterTitle: async (projectId, chapterId, data) => {
        aiCalls.push({ projectId, chapterId, data });
        return aiPlan;
      },
      updateMemoirBookChapterTitle: async (projectId, chapterId, data) => {
        manualCalls.push({ projectId, chapterId, data });
        return manualPlan;
      },
      getMemoirBookChapterDraft: async () => sampleDraft(1, "confirmed"),
      getMemoirBookOutput: async () => sampleBookOutput(),
    },
  });
  const context = pageContext(page, { projectId: "project/1", projectTitle: "外婆回忆录" });
  page.applyPlan.call(context, samplePlan("confirmed"));

  page.openChapterMoreActions.call(context, {
    currentTarget: { dataset: { id: "chapter-1" } },
  });
  await flushAsyncWork(12);

  assert.deepEqual(manualCalls[0], {
    projectId: "project/1",
    chapterId: "chapter-1",
    data: { title: "院子里的童年" },
  });
  assert.equal(context.data.chapters[0].title, "院子里的童年");

  page.openChapterMoreActions.call(context, {
    currentTarget: { dataset: { id: "chapter-1" } },
  });
  await flushAsyncWork(12);

  assert.deepEqual(aiCalls[0], {
    projectId: "project/1",
    chapterId: "chapter-1",
    data: {},
  });
  assert.equal(context.data.chapters[0].title, "外婆院中的日子");
  assert.equal(context.data.titleBusyTarget, "");
});

test("book page reorders chapters through structure command", async () => {
  const calls = [];
  let draftReads = 0;
  let outputReads = 0;
  let resolveCommand;
  const commandResponse = new Promise((resolve) => {
    resolveCommand = resolve;
  });
  global.wx = { showToast() {}, switchTab() {} };
  const { page } = loadBookPage({
    voiceApiExports: {
      applyMemoirBookPlanStructureCommand: async (projectId, data) => {
        calls.push({ projectId, data });
        return commandResponse;
      },
      getMemoirBookChapterDraft: async () => {
        draftReads += 1;
        return null;
      },
      getMemoirBookOutput: async () => {
        outputReads += 1;
        return null;
      },
    },
  });
  const context = pageContext(page, { projectId: "project/1", projectTitle: "外婆回忆录" });
  page.applyPlan.call(context, samplePlan("confirmed"));
  page.applyBookOutput.call(context, sampleBookOutput());

  const movePromise = page.moveChapter.call(context, {
    currentTarget: { dataset: { id: "chapter-1", direction: "down" } },
  });

  assert.deepEqual(context.data.chapters.map((chapter) => chapter.id), ["chapter-2", "chapter-1"]);
  assert.equal(context.data.updatingPlanStructure, true);
  assert.equal(context.data.planStructureBusyChapterId, "chapter-1");
  assert.equal(context.data.planStructureBusyDirection, "down");
  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0].data, {
    type: "move_chapter",
    chapter_id: "chapter-1",
    direction: "down",
  });
  resolveCommand(planFromStructureCommand("confirmed", calls[0].data));
  await movePromise;

  assert.deepEqual(context.data.chapters.map((chapter) => chapter.id), ["chapter-2", "chapter-1"]);
  assert.equal(context.data.updatingPlanStructure, false);
  assert.equal(context.data.planStructureBusyChapterId, "");
  assert.equal(context.data.planStructureBusyDirection, "");
  assert.equal(draftReads, 0);
  assert.equal(outputReads, 0);
  assert.equal(context.bookOutput.is_stale, true);
  assert.deepEqual(context.bookOutput.stale_chapter_titles, ["第一次离家", "外婆家的童年"]);
});

test("book page restores chapter order when structure save fails", async () => {
  global.wx = { showToast() {}, switchTab() {} };
  const { page } = loadBookPage({
    voiceApiExports: {
      applyMemoirBookPlanStructureCommand: async () => {
        throw new Error("保存顺序失败");
      },
    },
  });
  const context = pageContext(page, { projectId: "project/1", projectTitle: "外婆回忆录" });
  page.applyPlan.call(context, samplePlan("confirmed"));

  await page.moveChapter.call(context, {
    currentTarget: { dataset: { id: "chapter-1", direction: "down" } },
  });

  assert.deepEqual(context.data.chapters.map((chapter) => chapter.id), ["chapter-1", "chapter-2"]);
  assert.equal(context.data.error, "保存顺序失败");
  assert.equal(context.data.updatingPlanStructure, false);
});

test("book page splits selected chapter sources into a new chapter", async () => {
  const calls = [];
  global.wx = { showToast() {}, switchTab() {} };
  const plan = samplePlan("confirmed");
  plan.chapters[0].source_refs = [
    sampleRef("utterance-1", "外婆家的院子"),
    sampleRef("utterance-3", "春节做年糕"),
  ];
  const { page } = loadBookPage({
    voiceApiExports: {
      applyMemoirBookPlanStructureCommand: async (projectId, data) => {
        calls.push({ projectId, data });
        return planFromStructureCommand("confirmed", data);
      },
      getMemoirBookChapterDraft: async () => null,
      getMemoirBookOutput: async () => null,
    },
  });
  const context = pageContext(page, { projectId: "project/1", projectTitle: "外婆回忆录" });
  page.applyPlan.call(context, plan);

  page.toggleSplitSource.call(context, {
    currentTarget: { dataset: { id: "chapter-1" } },
  });
  assert.equal(context.data.chapters[0].splitSelectedCount, 0);

  page.toggleChapterSources.call(context, {
    currentTarget: { dataset: { id: "chapter-1" } },
  });
  page.toggleSplitSource.call(context, {
    currentTarget: { dataset: { id: "chapter-1", utteranceId: "utterance-3" } },
  });

  assert.equal(context.data.chapters[0].sourceViews[1].splitSelected, true);
  assert.equal(context.data.chapters[0].splitSelectedCount, 1);
  assert.equal(context.data.chapters[0].canSplitSelectedSources, true);

  await page.splitSelectedChapterSources.call(context, {
    currentTarget: { dataset: { id: "chapter-1" } },
  });

  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0].data, {
    type: "split_chapter",
    chapter_id: "chapter-1",
    title: "外婆家的童年（拆出）",
    utterance_ids: ["utterance-3"],
  });
  assert.equal(context.data.splitSourceSelections["chapter-1"], undefined);
});

test("book page merges chapters and directs the user back to the workspace", async () => {
  const calls = [];
  global.wx = {
    showModal(options) {
      options.success({ confirm: true });
    },
    showToast() {},
    switchTab() {},
  };
  const { page } = loadBookPage({
    voiceApiExports: {
      applyMemoirBookPlanStructureCommand: async (projectId, data) => {
        calls.push({ kind: "update", projectId, data });
        return planFromStructureCommand("confirmed", data);
      },
      generateMemoirBookChapterTitle: async (projectId, chapterId, data) => {
        calls.push({ kind: "title", projectId, chapterId, data });
        const updated = planFromStructureCommand("confirmed", calls[0].data);
        updated.chapters[0].title = "童年里的离别";
        return updated;
      },
      getMemoirBookChapterDraft: async () => null,
      getMemoirBookOutput: async () => null,
    },
  });
  const context = pageContext(page, { projectId: "project/1", projectTitle: "外婆回忆录" });
  page.applyPlan.call(context, samplePlan("confirmed"), {
    "chapter-1": sampleDraft(1, "confirmed", "童年正文"),
    "chapter-2": sampleDraft(1, "confirmed", "离家正文"),
  });

  await page.mergeChapterInto.call(context, {
    currentTarget: { dataset: { id: "chapter-2" } },
    detail: { value: 0 },
  });

  assert.deepEqual(calls.map((call) => call.kind), ["update", "title"]);
  assert.deepEqual(calls[0].data, {
    type: "merge_chapters",
    chapter_id: "chapter-2",
    target_chapter_id: "chapter-1",
  });
  assert.equal(context.data.chapters[0].title, "童年里的离别");
  assert.equal(context.data.chapters[0].draftError, "章节已合并，请进入章节工作台重新整理正文");
});

test("book page opens automatically assembled drafts without chapter confirmation", () => {
  const urls = [];
  global.wx = { navigateTo: ({ url }) => urls.push(url) };
  const { page } = loadBookPage();
  const context = pageContext(page, { projectId: "project-1", projectTitle: "外婆回忆录" });
  page.applyPlan.call(context, samplePlan("confirmed"), {
    "chapter-1": sampleDraft(1, "draft"), "chapter-2": sampleDraft(1, "draft"),
  });
  page.applyBookOutput.call(context, sampleBookOutput(1, "后台整理好的草稿"));
  page.openBookReader.call(context);
  assert.equal(context.data.bookContentAvailable, true);
  assert.ok(urls[0].startsWith("/pages/book-reader/index?"));
  assert.equal(urls.length, 1);
});

test("book page omits the misleading progress shortcut but keeps progress in tools", () => {
  const template = fs.readFileSync(path.join(__dirname, "../pages/book/index.wxml"), "utf8");
  assert.doesNotMatch(template, /后台整理进度|bindtap="openStory"/);
  assert.match(template, /class="automation-title">整理进度</);
});

test("book page sends TTS voice and speed for chapter and outline audio", async () => {
  const calls = [];
  const { page } = loadBookPage({
    voiceApiExports: {
      createMemoirBookChapterDraftTTS: async (projectId, chapterId, data) => {
        calls.push({ kind: "chapter", projectId, chapterId, data });
        return sampleAudioResponse();
      },
      createMemoirBookPlanOutlineTTS: async (projectId, data) => {
        calls.push({ kind: "outline", projectId, data });
        return sampleAudioResponse();
      },
    },
    ttsExports: {
      readBookTTSRequest: () => ({
        voice: "zh_male_m191_uranus_bigtts",
        speed_ratio: 1.5,
      }),
    },
  });
  const context = pageContext(page, { projectId: "project/1" });

  await page.requestBookAudio.call(context, "outline");
  await page.requestBookAudio.call(context, "chapter:chapter-1");

  assert.deepEqual(calls, [
    {
      kind: "outline",
      projectId: "project/1",
      data: {
        voice: "zh_male_m191_uranus_bigtts",
        speed_ratio: 1.5,
      },
    },
    {
      kind: "chapter",
      projectId: "project/1",
      chapterId: "chapter-1",
      data: {
        voice: "zh_male_m191_uranus_bigtts",
        speed_ratio: 1.5,
      },
    },
  ]);
});

test("book page shows chapter narration toast after preflight passes", async () => {
  const toasts = [];
  const { mod, page } = loadBookPage({
    voiceApiExports: {
      createMemoirBookChapterDraftTTS: async () => sampleAudioResponse(),
    },
  });
  global.wx = {
    showToast(options) {
      toasts.push(options.title);
    },
  };
  const chapters = mod.buildChapterViews(samplePlan("confirmed"), {
    "chapter-1": sampleDraft(1, "draft"),
  });
  const context = pageContext(page, { projectId: "project/1", chapters });
  context.bookDraftsByChapterId = {
    "chapter-1": sampleDraft(1, "draft"),
  };
  context.playAudioFiles = async () => {};

  await page.playChapterAudioById.call(context, "chapter-1", { showPreparingToast: true });

  assert.deepEqual(toasts, ["正在准备朗读"]);
});
