require("./register-ts");
const { memoirAudioPlanLoader } = require("../services/memoir-book-api.ts");
const { sampleAudioPlan } = require("./book-fixtures");

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const {
  clearModule,
  mockMemoirApiModules,
  flushAsyncWork,
  loadPageModule,
  mockModule,
  pageContext,
  unmockedAsync,
} = require("./page-harness");

const {
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
    memoirAudioPlanLoader: (...args) => memoirAudioPlanLoader(...args, voiceApiExports.prepareMemoirAudio || unmockedAsync("prepareMemoirAudio")),
    applyMemoirBookPlanStructureCommand: unmockedAsync("applyMemoirBookPlanStructureCommand"),
    assembleMemoirBookOutput: unmockedAsync("assembleMemoirBookOutput"),
    updateMemoirBookOutput: unmockedAsync("updateMemoirBookOutput"),
    downloadMemoirVoiceAudio: unmockedAsync("downloadMemoirVoiceAudio"),
    generateMemoirBookChapterTitle: unmockedAsync("generateMemoirBookChapterTitle"),
    updateMemoirBookChapterTitle: unmockedAsync("updateMemoirBookChapterTitle"),
    getMemoirBookChapterDraft: async () => null,
    getMemoirBookOutput: async () => null,
    getMemoirBookShareStatus: unmockedAsync("getMemoirBookShareStatus"),
    createMemoirBookShare: unmockedAsync("createMemoirBookShare"),
    updateMemoirBookShare: unmockedAsync("updateMemoirBookShare"),
    readMemoirBookShare: async () => ({ latest_version: null }),
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

  clearModule("../services/reading-share.ts");
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

test("book page binds separate settings and file entries", () => {
  const pageWxml = fs.readFileSync(path.join(__dirname, "../pages/book/index.wxml"), "utf8");
  const { page } = loadBookPage();

  assert.equal((pageWxml.match(/bindtap="openBookExportPage"/g) || []).length, 2);
  assert.match(pageWxml, /data-action="settings" bindtap="openBookExportPage"/);
  assert.match(pageWxml, /data-action="files" bindtap="openBookExportPage"/);
  assert.deepEqual(page.onShareAppMessage.call(pageContext(page)), {
    title: "老余口述，把家里的故事慢慢讲下来",
    path: "/pages/home/index?from=share_home",
  });
  assert.equal(page.onShareTimeline, undefined);
});

test("one share action prepares the default book, reuses its link, and can stop it", async () => {
  const calls = [];
  global.wx = { getStorageSync: () => undefined, showModal: ({ success }) => success({ confirm: true }) };
  const { page } = loadBookPage({ voiceApiExports: {
    getMemoirBookShareStatus: async () => ({ active: false, current: false, stale: false }),
    updateMemoirBookShare: async (...args) => { calls.push(["ensure", ...args]); return { token: "family-secret" }; },
    revokeMemoirBookShare: async (...args) => { calls.push(["stop", ...args]); },
  } });
  const context = pageContext(page, { projectId: "project/1", bookContentAvailable: true, bookPreviewTitle: "外婆回忆录" });
  await context.refreshBookShareStatus();
  assert.equal(calls.length, 0);
  await context.prepareReadingShare();
  await context.prepareReadingShare();
  assert.equal(context.data.bookShareReady, true);
  assert.deepEqual(context.onShareAppMessage(), {
    title: "外婆回忆录", path: "/pages/book-reader/index?shareToken=family-secret", imageUrl: "https://pawtale-api.spaceflows.cn/api/mobile/memoir/book-shares/family-secret/card.jpg?design=1",
  });
  await context.revokeBookShare();
  assert.deepEqual(calls, [["ensure", "project/1", undefined, undefined], ["stop", "project/1", undefined, undefined]]);
  assert.equal(context.data.bookShareReady, false);
  assert.equal(context.onShareAppMessage().path, "/pages/home/index?from=share_home");
});

test("existing current shares need no update or configuration", async () => {
  global.wx = { getStorageSync: () => undefined };
  const { page } = loadBookPage({ voiceApiExports: {
    getMemoirBookShareStatus: async () => ({ active: true, token: "existing", current: true, stale: false }),
  } });
  const context = pageContext(page, { projectId: "p", bookContentAvailable: true });
  await context.refreshBookShareStatus();
  assert.equal(context.data.bookShareReady, true);
  assert.equal(context.onShareAppMessage().path, "/pages/book-reader/index?shareToken=existing");
});

test("a failed preparation stays on the same action and can be retried", async () => {
  global.wx = { getStorageSync: () => undefined };
  let fail = true;
  const { page } = loadBookPage({ voiceApiExports: {
    updateMemoirBookShare: async () => { if (fail) throw new Error("网络暂时不可用"); return { token: "retry" }; },
  } });
  const context = pageContext(page, { projectId: "p", bookContentAvailable: true });
  await context.prepareReadingShare();
  assert.equal(context.data.bookShareReady, false);
  assert.equal(context.data.bookShareBusy, false);
  fail = false;
  await context.prepareReadingShare();
  assert.equal(context.data.bookShareReady, true);
});

test("a withdrawn source disables sharing until the server returns a safe version", async () => {
  const { page } = loadBookPage({ voiceApiExports: {
    getMemoirBookShareStatus: async () => ({ active: true, token: "existing", stale: true, current: false }),
  } });
  const context = pageContext(page, { projectId: "p", bookContentAvailable: true });
  await context.refreshBookShareStatus();
  assert.equal(context.data.bookShareActive, true);
  assert.equal(context.data.bookShareReady, false);
  assert.equal(context.onShareAppMessage().path, "/pages/home/index?from=share_home");
});

test("a new book version rechecks the same link and ignores a previous project's response", async () => {
  global.wx = { getStorageSync: () => undefined };
  let resolveStatus;
  const { page } = loadBookPage({ voiceApiExports: {
    getMemoirBookShareStatus: () => new Promise(resolve => { resolveStatus = resolve; }),
  } });
  const context = pageContext(page, { projectId: "p", bookShareReady: true, bookContentAvailable: true });
  context.bookOutput = sampleBookOutput(1);
  context.bookShareToken = "existing";
  context.applyBookOutput(sampleBookOutput(2));
  assert.equal(context.data.bookShareReady, false);
  resolveStatus({ active: true, token: "existing", current: true, stale: false });
  await flushAsyncWork();
  assert.equal(context.data.bookShareReady, true);
  const pending = context.refreshBookShareStatus();
  context.setData({ projectId: "other" });
  resolveStatus({ active: true, token: "previous-project", current: true, stale: false });
  await pending;
  assert.equal(context.data.bookShareReady, false);
});

test("friend-view preview navigates without activating a share", () => {
  const urls = [];
  global.wx = { navigateTo: ({url}) => urls.push(url) };
  const { page } = loadBookPage();
  const context = pageContext(page, { projectId: "project/1", bookContentAvailable: true });
  context.openReadingSharePreview();
  assert.deepEqual(urls, ["/pages/book-reader/index?projectId=project%2F1&sharePreview=1"]);
  assert.equal(context.data.bookShareActive, false);
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
  context.data.bookStaleNotice = "章节已更新";
  page.openBookReader.call(context);
  page.openBookExportPage.call(context, { currentTarget: { dataset: { action: "files" } } });

  assert.deepEqual(navigations, [
    "/pages/book-reader/index?projectId=project%2F1",
    "/pages/book-reader/index?projectId=project%2F1",
    "/pages/book-export/index?projectId=project%2F1&action=files",
  ]);
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
  assert.equal(chapters[0].chapterGuidanceText, "");
  assert.equal(chapters[1].chapterNeedsAction, false);
  assert.equal(chapters[1].chapterGuidanceText, "");
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
  assert.match(chapter.chapterGuidanceText, /没有正文.*章节页.*当前步骤/);
  assert.doesNotMatch(chapter.chapterSummaryMetaText, /未生成|待确认/);

  const planWithNewSource = samplePlan("confirmed");
  planWithNewSource.chapters[0].pending_source_count = 1;
  const [unpreparedChapter] = mod.buildChapterViews(planWithNewSource);
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
  assert.match(data.bookStaleNotice, /外婆家的童年.*第一次离家.*尚未更新/);
  assert.match(data.bookStaleNotice, /书稿建议/);

  page.applyBookOutput.call(context, output, { status: "needs_input" });
  assert.match(context.data.bookStaleNotice, /书稿建议.*尚未更新/);
  page.applyBookOutput.call(context, output, { status: "processing" });
  assert.match(context.data.bookStaleNotice, /正在整理新版全书/);
  assert.equal("bookPreviewChapters" in data, false);
  assert.equal("bookTitleInput" in data, false);
  assert.equal("bookOutput" in data, false);
  assert.equal("bookContent" in data, false);

  page.applyBookOutput.call(
    context,
    sampleBookOutput(4, "当前可读的书稿", false, [], [], sampleBookManuscript("外婆回忆录", "classic_plain", { chapterCount: 1 })),
    { status: "needs_input", total_chapter_count: 2 },
  );
  assert.equal(context.data.bookContentAvailable, true);
  assert.equal(context.data.bookIncludedChapterCount, 1);
  assert.equal(context.data.bookStaleNotice, "");

  page.applyBookOutput.call(context, sampleBookOutput(4, "外婆回忆录\n\n目录", true, ["外婆家的童年"]));
  assert.equal(context.data.bookContentAvailable, false);
  assert.equal(context.data.bookCharCount, 0);
  const template = fs.readFileSync(path.join(__dirname, "../pages/book/index.wxml"), "utf8");
  assert.match(template, /wx:if="\{\{bookContentAvailable\}\}" is="readingShareActions"[^>]*showBookReader: true/);

  const previousOutput = context.bookOutput;
  page.applyPlan.call(context, samplePlan("draft"));
  assert.equal(context.bookOutput, previousOutput);
  assert.equal(context.data.planNeedsActivation, true);
  assert.equal(context.data.bookContentAvailable, false);
});

test("book page identifies a drafted chapter missing from the current book", () => {
  const { page } = loadBookPage();
  const context = pageContext(page);
  const secondDraft = sampleDraft(1, "draft");
  secondDraft.chapter_id = "chapter-2";
  secondDraft.latest_version.chapter_id = "chapter-2";
  page.applyPlan.call(context, samplePlan("confirmed"), {
    "chapter-1": sampleDraft(1, "draft"),
    "chapter-2": secondDraft,
  });
  const partialOutput = sampleBookOutput(4, "当前可读的书稿", false, [], [], sampleBookManuscript("外婆回忆录", "classic_plain", { chapterCount: 1 }));
  const needsInput = { status: "needs_input", total_chapter_count: 2 };
  page.applyBookOutput.call(context, partialOutput, needsInput);

  assert.equal(context.data.readyDraftCount, 2);
  assert.equal(context.data.bookIncludedChapterCount, 1);
  assert.equal(context.data.chapters[0].bookInclusionNotice, "");
  assert.match(context.data.chapters[1].bookInclusionNotice, /本章待确认.*未收入当前书稿.*书稿建议/);

  const planWithNewSource = samplePlan("confirmed");
  planWithNewSource.chapters[1].pending_source_count = 1;
  planWithNewSource.chapters[1].pending_new_source_count = 1;
  page.applyPlan.call(context, planWithNewSource, {
    "chapter-1": sampleDraft(1, "draft"),
    "chapter-2": secondDraft,
  });
  page.applyBookOutput.call(context, partialOutput, needsInput);
  assert.match(context.data.chapters[1].bookInclusionNotice, /新增讲述尚未写入正文/);

  page.applyBookOutput.call(context, sampleBookOutput(5, "全书正文", false, [], [], sampleBookManuscript()));
  assert.equal(context.data.chapters[1].bookInclusionNotice, "");
});

test("book page exposes an empty omitted chapter and guides additional narration", () => {
  const { page } = loadBookPage();
  const context = pageContext(page);
  const plan = samplePlan("confirmed");
  plan.chapters[1].source_refs = [];
  page.applyPlan.call(context, plan, { "chapter-1": sampleDraft(1, "draft") });
  const partialOutput = sampleBookOutput(4, "当前书稿", false, [], [], sampleBookManuscript("外婆回忆录", "classic_plain", { chapterCount: 1 }));

  page.applyBookOutput.call(context, partialOutput, { status: "needs_input", needs_user_material_count: 1 });

  assert.equal(context.data.chapters[1].draftAvailable, false);
  assert.match(context.data.chapters[1].chapterGuidanceText, /没有讲述素材.*继续讲述/);
  assert.match(context.data.chapters[1].bookInclusionNotice, /未收入当前书稿.*首页.*讲述/);

  page.applyBookOutput.call(context, partialOutput, { status: "failed" });
  assert.match(context.data.chapters[1].bookInclusionNotice, /遇到问题.*重试/);
  assert.equal(context.data.bookIncludedChapterCount, 1);
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
    blocking_issue_count: 3,
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
  assert.equal(context.data.blockingIssueCount, 3);
  assert.equal(context.data.projectTitle, "外婆回忆录");
  assert.equal(context.data.showingCachedBook, true);
  assert.equal(context.data.error, "");

  await page.loadBookHome.call(context);

  assert.equal(fetchCount, 2);
  assert.equal(context.data.showingCachedBook, false);
});

test("book page refreshes mandatory confirmation counts without changing saved book content", () => {
  const { page } = loadBookPage();
  const context = pageContext(page);
  const snapshot = {
    identity: { active_project_id: "project/1", active_project: { title: "外婆回忆录" } },
    plan: samplePlan("confirmed"), drafts_by_chapter_id: { "chapter-1": sampleDraft() },
    output: sampleBookOutput(4, "外婆回忆录\n\n目录"), blocking_issue_count: 3,
    automation_events: { items: [] },
    image_assets: { items: [], total: 0, ready_count: 0, included_count: 0, unused_count: 0 },
  };
  page.applyBookSnapshot.call(context, snapshot);
  assert.equal(context.data.blockingIssueCount, 3);
  assert.equal(context.data.bookContentAvailable, false);
  assert.equal(context.data.bookCharCount, 0);
  assert.equal(context.bookOutput, snapshot.output);

  page.applyBookSnapshot.call(context, { ...snapshot, blocking_issue_count: 0 });
  assert.equal(context.data.blockingIssueCount, 0);
  assert.equal(context.bookOutput, snapshot.output);
  const template = fs.readFileSync(path.join(__dirname, "../pages/book/index.wxml"), "utf8");
  assert.match(template, /bindtap="openBookCompletion"[\s\S]*?blockingIssueCount/);
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
  assert.equal(chapters[0].chapterNeedsAction, true);
});

test("book page shows the number of visible sections in each chapter", () => {
  const { mod } = loadBookPage();
  const chapters = mod.buildChapterViews(samplePlan("confirmed"), {
    "chapter-1": sampleDraft(1, "confirmed", "第一版正文"),
  });

  assert.equal(chapters[0].sectionCount, 1);
  assert.equal(chapters[0].sectionCount, 1);
  assert.doesNotMatch(chapters[0].chapterSummaryMetaText, /口述/);
  assert.equal(chapters[0].draftCharCount, 5);
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

  plan.chapters[0].pending_source_count = 1;
  plan.chapters[0].pending_new_source_count = 1;
  const [changedChapter] = mod.buildChapterViews(plan, {
    "chapter-1": sampleDraft(1, "draft", "第一版正文"),
  });
  assert.match(changedChapter.chapterGuidanceText, /1 条讲述已删除，1 条新增讲述尚未写入正文/);
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


test("book outline uses the shared audio plan and saved voice settings", async () => {
  const calls = [], played = [];
  const { page } = loadBookPage({ voiceApiExports: {
    prepareMemoirAudio: async (...args) => { calls.push(args); return sampleAudioPlan(); },
  }, ttsExports: { readBookTTSRequest: () => ({ voice: "cixingnansheng", speed_ratio: 1.5 }) } });
  const context = pageContext(page, { projectId: "project/1", chapters: [{ title: "童年" }] });
  context.bookPlan = { title: "回忆录" };
  context.audioPlayback.playFile = async (id) => { played.push(id); };
  await page.playBookOutlineAudio.call(context);
  assert.deepEqual(calls, [["project/1", { kind: "outline" }, {
    voice: "cixingnansheng", speed_ratio: 1.5, start_index: 0, retry_failed: true,
  }]]);
  assert.deepEqual(played, ["audio-1"]);
});

test("book overview routes settings before a draft and keeps export unavailable until readable", () => {
  const urls = [];
  global.wx = { navigateTo: ({ url }) => urls.push(url) };
  const { page } = loadBookPage();
  const context = pageContext(page, { projectId: "project-1", bookContentAvailable: false });
  page.openBookExportPage.call(context, { currentTarget: { dataset: { action: "settings" } } });
  page.openBookExportPage.call(context, { currentTarget: { dataset: { action: "files" } } });
  assert.deepEqual(urls, ["/pages/book-export/index?projectId=project-1&action=settings"]);
  context.setData({ bookContentAvailable: true });
  page.openBookExportPage.call(context, { currentTarget: { dataset: { action: "files" } } });
  assert.equal(urls[1], "/pages/book-export/index?projectId=project-1&action=files");
  const template = fs.readFileSync(path.join(__dirname, "../pages/book/index.wxml"), "utf8");
  assert.match(template, /<view class="book-operation-list ui-card">/);
});

test("book overview preserves processing and failure notices without marking settled chapters as unread", () => {
  const { page } = loadBookPage();
  const context = pageContext(page);
  const snapshot = {
    identity: { active_project_id: "project/1", active_project: { title: "外婆回忆录" } },
    plan: samplePlan("confirmed"),
    drafts_by_chapter_id: { "chapter-1": sampleDraft(1, "draft") },
    output: null,
    automation_events: { items: [sampleAutomationEvent({ status: "pending" })] },
  };
  page.applyBookSnapshot.call(context, snapshot);
  assert.equal(context.data.chapters[0].chapterActivityText, "正在整理");
  snapshot.automation_events.items[0].status = "failed";
  page.applyBookSnapshot.call(context, snapshot);
  assert.match(context.data.chapters[0].chapterActivityText, /整理失败.*正文仍可阅读/);
  snapshot.automation_events.items[0].status = "completed";
  page.applyBookSnapshot.call(context, snapshot);
  assert.equal(context.data.chapters[0].chapterActivityText, "");
  assert.equal(context.data.chapters[0].chapterGuidanceText, "");
  assert.match(context.data.chapters[1].chapterGuidanceText, /没有正文/);
  snapshot.automation_events.items = [
    sampleAutomationEvent({ status: "failed", updated_at: "2026-10-01T00:00:00Z" }),
    sampleAutomationEvent({ status: "completed", updated_at: "2026-10-02T00:00:00Z" }),
  ];
  page.applyBookSnapshot.call(context, snapshot);
  assert.equal(context.data.chapters[0].chapterActivityText, "");
  assert.equal(context.data.bookActivityNotice, "");
  snapshot.progress = { status: "processing", title: "正在整理新版书稿" };
  page.applyBookSnapshot.call(context, snapshot);
  assert.equal(context.data.bookActivityNotice, "正在整理新版书稿");
});

test("pending narration shows the server detail without leaving the book or hiding active or failed work", async () => {
  const urls = [];
  const dialogs = [];
  global.wx = {
    reLaunch: ({ url }) => urls.push(url),
    navigateTo: ({ url }) => urls.push(url),
    showModal: (options) => { dialogs.push(options); options.success({ confirm: false }); },
  };
  const { page } = loadBookPage();
  const context = pageContext(page);
  const snapshot = {
    identity: { active_project_id: "project/1", active_project: { title: "外婆回忆录" } },
    plan: samplePlan("confirmed"), drafts_by_chapter_id: {}, output: null,
    automation_events: { items: [] },
    progress: { status: "partial_ready", pending_material_count: 3, title: "待整理",
      detail: "讲述已经保存，可以继续整理；已有正文仍可阅读。", can_retry: true },
  };
  page.applyBookSnapshot.call(context, snapshot);
  assert.equal(context.data.bookPendingMaterialCount, 3);
  assert.match(context.data.bookActivityNotice, /3/);
  await context.openBookActivity();
  assert.equal(dialogs.at(-1).content, snapshot.progress.detail);
  assert.equal(dialogs.at(-1).confirmText, "继续整理");
  assert.deepEqual(urls, []);

  snapshot.plan = null;
  snapshot.progress.status = "no_change";
  snapshot.progress.pending_material_count = 2;
  page.applyBookSnapshot.call(context, snapshot);
  assert.equal(context.data.bookPendingMaterialCount, 2);
  await context.openBookActivity();
  assert.equal(dialogs.length, 2);
  assert.deepEqual(urls, []);

  snapshot.plan = samplePlan("confirmed");
  for (const status of ["processing", "failed", "needs_input"]) {
    snapshot.progress.status = status;
    snapshot.progress.title = `${status} notice`;
    page.applyBookSnapshot.call(context, snapshot);
    assert.equal(context.data.bookPendingMaterialCount, 0);
    assert.equal(context.data.bookActivityNotice, snapshot.progress.title);
    await context.openBookActivity();
    assert.match(urls.pop(), /^\/pages\/book-completion\/index\?/);
  }

  snapshot.progress = { status: "ready", pending_material_count: 0 };
  page.applyBookSnapshot.call(context, snapshot);
  assert.equal(context.data.bookPendingMaterialCount, 0);
  assert.equal(context.data.bookActivityNotice, "");
});

test("pending narration resumes the existing draft job only when allowed and confirmed", async () => {
  const calls = [];
  let confirm = false;
  global.wx = {
    showToast() {},
    showModal: (options) => options.success({ confirm }),
  };
  const { page } = loadBookPage({ voiceApiExports: {
    retryMemoirDraftProgress: async (projectId) => { calls.push(projectId); return {}; },
  } });
  const context = pageContext(page);
  const snapshot = {
    identity: { active_project_id: "project/1", active_project: { title: "回忆录" } },
    plan: samplePlan("confirmed"), drafts_by_chapter_id: {}, output: null,
    automation_events: { items: [] },
    progress: { status: "partial_ready", pending_material_count: 1, can_retry: true,
      detail: "讲述已经保存，可以继续整理；已有正文仍可阅读。" },
  };
  page.applyBookSnapshot.call(context, snapshot);
  context.loadBookHome = async () => { calls.push("refresh"); };

  await context.openBookActivity();
  assert.deepEqual(calls, []);
  confirm = true;
  await context.openBookActivity();
  assert.deepEqual(calls, ["project/1", "refresh"]);
  assert.equal(context.data.retryingDraft, false);

  snapshot.progress.can_retry = false;
  snapshot.progress.detail = "还需要补充背景信息。";
  page.applyBookSnapshot.call(context, snapshot);
  let dialog;
  global.wx.showModal = (options) => { dialog = options; options.success({ confirm: true }); };
  await context.openBookActivity();
  assert.equal(dialog.content, snapshot.progress.detail);
  assert.equal(dialog.showCancel, false);
  assert.equal(dialog.confirmText, "知道了");
  assert.deepEqual(calls, ["project/1", "refresh"]);

  snapshot.progress.can_retry = true;
  page.applyBookSnapshot.call(context, snapshot);
  global.wx.showModal = (options) => options.fail();
  await context.openBookActivity();
  assert.deepEqual(calls, ["project/1", "refresh"]);

  global.wx.showModal = (options) => {
    context.setData({ projectId: "project/2" });
    options.success({ confirm: true });
  };
  await context.openBookActivity();
  assert.deepEqual(calls, ["project/1", "refresh"]);
});


test("legacy outline continues through automatic writing without replacing its structure", async () => {
  const calls = [];
  global.wx = { showToast() {} };
  const { page } = loadBookPage({ voiceApiExports: {
    retryMemoirDraftProgress: async (projectId) => { calls.push(projectId); return {}; },
  } });
  const context = pageContext(page, { projectId: "project/1" });
  const plan = samplePlan("draft");
  plan.chapters[0].title = "用户保留的标题";
  plan.chapters[1].include_in_book = false;
  page.applyPlan.call(context, plan);
  context.loadBookHome = async () => {};
  assert.equal(context.data.stage, "drafting");
  assert.equal(context.data.planNeedsActivation, true);
  await page.continueBookDraft.call(context);
  assert.deepEqual(calls, ["project/1"]);
  assert.equal(context.data.chapters[0].title, "用户保留的标题");
  assert.equal(context.data.chapters[1].include_in_book, false);
  assert.equal(context.data.retryingDraft, false);
});

test("continue automatic writing reports failure and permits retry", async () => {
  const { page } = loadBookPage({ voiceApiExports: {
    retryMemoirDraftProgress: async () => { throw new Error("网络暂时不可用"); },
  } });
  const context = pageContext(page, { projectId: "project/1" });
  await page.continueBookDraft.call(context);
  assert.equal(context.data.error, "网络暂时不可用");
  assert.equal(context.data.retryingDraft, false);
});


test("an activated link remains stoppable when its card read fails", async () => {
  global.wx = { showModal: ({success}) => success({confirm: true}) };
  let stopped = false;
  const { page } = loadBookPage({ voiceApiExports: {
    updateMemoirBookShare: async () => ({ token: "active" }),
    readMemoirBookShare: async () => { throw new Error("network"); },
    revokeMemoirBookShare: async () => { stopped = true; },
  } });
  const context = pageContext(page, { projectId: "p", bookContentAvailable: true });
  await context.prepareReadingShare();
  assert.equal(context.data.bookShareReady, false);
  assert.equal(context.data.bookShareActive, true);
  await context.revokeBookShare();
  assert.equal(stopped, true);
});
