require("./register-ts");

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const {
  flushAsyncWork,
  loadPageModule,
  mockModule,
  pageContext,
} = require("./page-harness");
const { storyViewData } = require("../pages/story/story-view-adapter.ts");

function source(utteranceId) {
  return { utterance_id: utteranceId };
}

function section(id, sortOrder, utteranceIds, patch = {}) {
  return {
    id,
    title: `小节 ${id}`,
    status: "active",
    sort_order: sortOrder,
    material_count: utteranceIds.length,
    materials: utteranceIds.map((utteranceId) => ({ utterance_id: utteranceId })),
    ...patch,
  };
}

function chapter(id, sortOrder, utteranceIds, sections, patch = {}) {
  return {
    id,
    title: `章节 ${id}`,
    sort_order: sortOrder,
    include_in_book: true,
    source_refs: utteranceIds.map(source),
    sections,
    pending_source_count: 0,
    removed_source_count: 0,
    ...patch,
  };
}

function image(id, patch = {}) {
  return {
    id,
    status: "ready",
    source_utterance_id: null,
    source_chapter_id: null,
    updated_at: "2026-08-03T00:00:00Z",
    ...patch,
  };
}

function snapshot(patch = {}) {
  const firstChapter = chapter(
    "chapter-1",
    1,
    ["utterance-1", "utterance-2"],
    [
      section("section-1", 1, ["utterance-1"]),
      section("section-archived", 2, ["utterance-2"], { status: "archived" }),
    ],
  );
  const secondChapter = chapter(
    "chapter-2",
    2,
    ["utterance-3"],
    [section("section-2", 1, ["utterance-3"])],
    { include_in_book: false },
  );
  return {
    identity: {
      active_project_id: "project-1",
      active_project: {
        id: "project-1",
        title: "余仲尤的回忆录",
        created_at: "2026-08-01T00:00:00Z",
        updated_at: "2026-08-02T00:00:00Z",
      },
    },
    plan: {
      updated_at: "2026-08-03T00:00:00Z",
      chapters: [secondChapter, firstChapter],
    },
    drafts_by_chapter_id: {
      "chapter-1": {
        status: "confirmed",
        pending_source_count: 0,
        removed_source_count: 0,
        latest_version: { content: "已经写好的章节正文" },
        updated_at: "2026-08-04T00:00:00Z",
      },
      "chapter-2": null,
    },
    output: null,
    image_assets: {
      items: [
        image("section-image", { source_utterance_id: "utterance-1" }),
        image("chapter-image", { source_chapter_id: "chapter-1" }),
        image("unsectioned-image", { source_utterance_id: "utterance-2" }),
        image("unassigned-image", { source_utterance_id: "utterance-4" }),
        image("placed-manual-image", {
          placements: [{ enabled: true, chapter_id: "chapter-1" }],
        }),
        image("unassigned-manual-image", { placements: [] }),
        image("pending-image", { status: "pending", source_chapter_id: "chapter-1" }),
      ],
    },
    share_video_counts: [
      { utterance_id: "utterance-1", count: 2, job_ids: ["video-1", "video-2"] },
      { utterance_id: "utterance-2", count: 1, job_ids: ["video-3"] },
      { utterance_id: "utterance-5", count: 1, job_ids: ["video-4"] },
    ],
    ...patch,
  };
}

function progress(patch = {}) {
  return {
    utterance_count: 6,
    snapshot: {
      valid_utterance_count: 5,
      pending_utterance_count: 2,
    },
    ...patch,
  };
}

function loadStoryPage({
  bookSnapshot = snapshot(),
  voiceProgress = progress(),
  snapshotError = null,
  largeText = false,
} = {}) {
  const calls = {
    book: 0,
    bookTargets: [],
    login: 0,
    transcripts: 0,
    progressProjectIds: [],
  };
  mockModule("../services/accessibility-settings.ts", {
    accessibilityPageClass: (baseClass) => largeText ? `${baseClass} large-text` : baseClass,
  });
  mockModule("../services/book-workspace-navigation.ts", {
    writeBookWorkspaceNavigationTarget: (target) => {
      calls.bookTargets.push(target);
    },
  });
  mockModule("../services/memoir-snapshot-api.ts", {
    getMemoirBookSnapshot: async () => {
      if (snapshotError) {
        throw snapshotError;
      }
      return bookSnapshot;
    },
  });
  mockModule("../services/memoir-voice-session-api.ts", {
    getMemoirVoiceProgress: async (projectId) => {
      calls.progressProjectIds.push(projectId);
      return voiceProgress;
    },
  });
  mockModule("../services/navigation.ts", {
    CHAPTER_WORKSPACE_PAGE_URL: "/pages/chapter-workspace/index",
    isAuthError: (error) => error?.statusCode === 401 || error?.statusCode === 403,
    navigateToBook: () => { calls.book += 1; },
    navigateToLogin: () => { calls.login += 1; },
    navigateToTranscripts: () => { calls.transcripts += 1; },
  });
  return { ...loadPageModule("../pages/story/index.ts"), calls };
}

test("story page replaces record and book with the three-tab product navigation", () => {
  const appJson = JSON.parse(fs.readFileSync(path.join(__dirname, "../app.json"), "utf8"));

  assert.ok(appJson.pages.includes("pages/story/index"));
  assert.deepEqual(
    appJson.tabBar.list.map(({ pagePath, text }) => ({ pagePath, text })),
    [
      { pagePath: "pages/home/index", text: "讲述" },
      { pagePath: "pages/story/index", text: "故事" },
      { pagePath: "pages/me/index", text: "我的" },
    ],
  );
  appJson.tabBar.list.forEach(({ iconPath, selectedIconPath }) => {
    assert.ok(fs.existsSync(path.join(__dirname, "..", iconPath)));
    assert.ok(fs.existsSync(path.join(__dirname, "..", selectedIconPath)));
  });
});

test("story adapter maps current chapters, outcomes and existing image ownership", () => {
  const view = storyViewData(snapshot(), progress());

  assert.equal(view.memoir.title, "余仲尤的回忆录");
  assert.equal(view.memoir.progressLabel, "书稿");
  assert.equal(view.memoir.progressValue, "1 章");
  assert.equal(view.memoir.progressDetail, "已整理");
  assert.equal(view.overview.summaryText, "2 章 · 2 小节 · 5 段讲述");
  assert.equal(view.unassignedMaterialText, "已经记录了 2 段讲述");
  assert.equal(view.unassignedMaterialDetail, "小余会把它们整理成人生故事");
  assert.deepEqual(view.chapters.map((item) => item.id), ["chapter-1", "chapter-2"]);
  assert.equal(view.chapters[0].sections.length, 1);
  assert.equal(view.chapters[0].sections[0].imageCount, 1);
  assert.equal(view.chapters[0].sections[0].videoCount, 2);
  assert.equal(view.chapters[0].imageCount, 3);
  assert.equal(view.chapters[0].videoCount, 1);
  assert.equal(view.chapters[1].outcomeText, "暂不入书");
  assert.equal(view.chapters[0].state, "settled");
  assert.equal(view.chapters[1].state, "muted");
  assert.equal(view.unassignedImageCount, 2);
  assert.equal(view.unassignedVideoCount, 1);
  assert.equal(view.chapters[0].expanded, true);
  assert.equal(view.chapters[1].expanded, false);
});

test("story adapter falls back to current source assignments when progress has no snapshot", () => {
  const view = storyViewData(snapshot(), { utterance_count: 5 });

  assert.equal(view.overview.summaryText, "2 章 · 2 小节 · 5 段讲述");
  assert.equal(view.unassignedMaterialText, "已经记录了 2 段讲述");
});

test("story adapter handles a memoir before chapters have been generated", () => {
  const view = storyViewData(snapshot({ plan: null, drafts_by_chapter_id: {} }), progress());

  assert.equal(view.memoir.progressValue, "尚未生成");
  assert.equal(view.memoir.progressDetail, "开始整理");
  assert.equal(view.chapters.length, 0);
  assert.equal(view.overview.summaryText, "0 章 · 0 小节 · 5 段讲述");
  assert.equal(view.unassignedVideoCount, 4);
});

test("story adapter distinguishes an empty memoir from fully assigned narration", () => {
  const empty = storyViewData(
    snapshot({ plan: null, drafts_by_chapter_id: {} }),
    progress({ utterance_count: 0, snapshot: { valid_utterance_count: 0, pending_utterance_count: 0 } }),
  );
  const assigned = storyViewData(snapshot(), progress({
    snapshot: { valid_utterance_count: 3, pending_utterance_count: 0 },
  }));

  assert.equal(empty.unassignedMaterialText, "还没有讲述");
  assert.match(empty.unassignedMaterialDetail, /第一段回忆/);
  assert.equal(assigned.unassignedMaterialText, "所有讲述都已归入故事");
  assert.match(assigned.unassignedMaterialDetail, /继续讲下新的回忆/);
});

test("story page loads the active project and preserves a user's expanded chapters", async () => {
  const { page, calls } = loadStoryPage({ largeText: true });
  const context = pageContext(page);
  global.wx = {};

  await page.onShow.call(context);

  assert.match(context.data.pageClass, /large-text/);
  assert.deepEqual(calls.progressProjectIds, ["project-1"]);
  assert.equal(context.data.projectId, "project-1");
  assert.equal(context.data.chapters[0].expanded, true);

  page.toggleChapter.call(context, {
    currentTarget: { dataset: { id: "chapter-1" } },
  });
  assert.equal(context.data.chapters[0].expanded, false);

  await page.loadStory.call(context);
  assert.equal(context.data.chapters[0].expanded, false);
});

test("story page does not request progress before a memoir exists", async () => {
  const bookSnapshot = snapshot({
    identity: { active_project_id: "", active_project: null },
    plan: null,
    drafts_by_chapter_id: {},
  });
  const { page, calls } = loadStoryPage({ bookSnapshot });
  const context = pageContext(page);

  await page.loadStory.call(context);

  assert.equal(context.data.projectId, "");
  assert.equal(context.data.loading, false);
  assert.deepEqual(calls.progressProjectIds, []);
});

test("story page exposes login state after an authentication error", async () => {
  const { page } = loadStoryPage({ snapshotError: { statusCode: 401 } });
  const context = pageContext(page);

  await page.loadStory.call(context);

  assert.equal(context.data.needsLogin, true);
  assert.equal(context.data.error, "");
});

test("story page reuses the existing book, record and chapter destinations", async () => {
  const { page, calls } = loadStoryPage();
  const context = pageContext(page, { projectId: "project-1" });
  const navigated = [];
  global.wx = {
    navigateTo: ({ url }) => navigated.push(url),
  };

  page.openBook.call(context);
  page.openChapterManager.call(context);
  page.openTranscripts.call(context);
  page.openChapter.call(context, {
    currentTarget: { dataset: { id: "chapter-1" } },
  });
  page.openChapter.call(context, {
    currentTarget: {
      dataset: { chapterId: "chapter-1", sectionId: "section-1" },
    },
  });
  page.openMedia.call(context, {
    currentTarget: { dataset: { id: "chapter-1" } },
  });
  page.openMedia.call(context, {
    currentTarget: {
      dataset: { chapterId: "chapter-1", sectionId: "section-1" },
    },
  });
  page.openMedia.call(context, { currentTarget: { dataset: {} } });
  page.openLogin.call(context);
  await flushAsyncWork();

  assert.equal(calls.book, 2);
  assert.deepEqual(calls.bookTargets, ["chapters"]);
  assert.equal(calls.login, 1);
  assert.equal(calls.transcripts, 1);
  assert.deepEqual(navigated, [
    "/pages/chapter-workspace/index?projectId=project-1&chapterId=chapter-1",
    "/pages/chapter-workspace/index?projectId=project-1&chapterId=chapter-1&sectionId=section-1",
    "/pages/image-assets/index?projectId=project-1&scope=chapter&chapterId=chapter-1",
    "/pages/image-assets/index?projectId=project-1&scope=section&chapterId=chapter-1&sectionId=section-1",
    "/pages/image-assets/index?projectId=project-1&scope=unassigned",
  ]);
});
