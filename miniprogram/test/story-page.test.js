require("./register-ts");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const { flushAsyncWork, loadPageModule, mockModule, pageContext } = require("./page-harness");
const { storyViewData } = require("../pages/story/story-view-adapter.ts");

function progress(patch = {}) {
  return { project_id: "project-1", status: "ready", title: "可读草稿已更新", detail: "全书检查已完成",
    readable_chapter_count: 1, total_chapter_count: 1, material_count: 5, pending_material_count: 2,
    needs_user_material_count: 1, can_retry: false, ...patch };
}
function snapshot(patch = {}) {
  return {
    identity: { active_project_id: "project-1", active_project: { id: "project-1", title: "余仲尤的回忆录" } },
    progress: progress(), book_readable: true, book_char_count: 200, updated_at: "2026-09-05T00:00:00Z",
    chapters: [1, 2].map((n) => ({ id: `chapter-${n}`, title: `章节 ${n}`, include_in_book: n === 1,
      material_count: 2, char_count: n === 1 ? 200 : 0, readable: n === 1,
      state: n === 1 ? "settled" : "muted", state_text: n === 1 ? "可阅读" : "暂不入书",
      image_count: 3, video_count: 1,
      sections: [{ id: `section-${n}`, title: `小节 ${n}`, material_count: 1, image_count: 1, video_count: 2 }],
    })),
    automation_events: { items: [], total: 0, limit: 8 },
    unassigned_image_count: 2, unassigned_video_count: 1, ...patch,
  };
}
function loadStoryPage({ current = snapshot(), getSnapshot, retry, largeText = false } = {}) {
  const calls = { book: 0, bookTargets: [], login: 0, transcripts: 0, acknowledgedEventIds: [] };
  mockModule("../services/accessibility-settings.ts", { accessibilityPageClass: (base) => largeText ? `${base} large-text` : base });
  mockModule("../services/book-workspace-navigation.ts", { writeBookWorkspaceNavigationTarget: (target) => calls.bookTargets.push(target) });
  mockModule("../services/memoir-snapshot-api.ts", {
    getMemoirStorySnapshot: getSnapshot || (async () => current),
    retryMemoirDraftProgress: retry || (async () => progress({ status: "processing" })),
  });
  mockModule("../services/memoir-voice-session-api.ts", {
    acknowledgeMemoirAutomationEvent: async (_, id) => { calls.acknowledgedEventIds.push(id); },
  });
  mockModule("../services/navigation.ts", {
    CHAPTER_WORKSPACE_PAGE_URL: "/pages/chapter-workspace/index",
    isAuthError: (error) => [401, 403].includes(error?.statusCode),
    navigateToBook: () => calls.book++, navigateToLogin: () => calls.login++, navigateToTranscripts: () => calls.transcripts++,
  });
  global.wx = {};
  return { ...loadPageModule("../pages/story/index.ts"), calls };
}

test("story is registered in the three-tab navigation", () => {
  const app = JSON.parse(fs.readFileSync(path.join(__dirname, "../app.json"), "utf8"));
  assert.ok(app.pages.includes("pages/story/index"));
  assert.deepEqual(app.tabBar.list.map(({ text }) => text), ["讲述", "故事", "我的"]);
  app.tabBar.list.forEach(({ iconPath, selectedIconPath }) => {
    assert.ok(fs.existsSync(path.join(__dirname, "..", iconPath)));
    assert.ok(fs.existsSync(path.join(__dirname, "..", selectedIconPath)));
  });
});

test("story maps server counts and distinguishes pending from user decisions", () => {
  const view = storyViewData(snapshot());
  assert.equal(view.memoir.progressDetail, "阅读草稿");
  assert.equal(view.overview.summaryText, "2 章 · 2 小节 · 5 段讲述");
  assert.match(view.unassignedMaterialDetail, /1 段需要您补充或确认/);
  assert.equal(view.chapters[0].readable, true);
  assert.equal(view.chapters[0].sections[0].imageCount, 1);
  assert.equal(view.chapters[0].sections[0].videoCount, 2);
  assert.equal(view.chapters[1].outcomeText, "暂不入书");
  assert.equal(view.unassignedImageCount, 2);
  assert.equal(view.unassignedVideoCount, 1);
});

test("story distinguishes empty and fully assigned material", () => {
  const empty = storyViewData(snapshot({ chapters: [], book_readable: false, progress: null }));
  assert.equal(empty.unassignedMaterialText, "还没有讲述");
  assert.equal(empty.memoir.progressValue, "尚未生成");
  const assigned = storyViewData(snapshot({ progress: progress({ pending_material_count: 0, needs_user_material_count: 0 }) }));
  assert.equal(assigned.unassignedMaterialText, "所有讲述都已归入故事");
});

test("story keeps expansion while refreshing the same project", async () => {
  const { page } = loadStoryPage({ largeText: true });
  const context = pageContext(page);
  await context.onShow();
  assert.match(context.data.pageClass, /large-text/);
  context.toggleChapter({ currentTarget: { dataset: { id: "chapter-1" } } });
  await context.loadStory();
  assert.equal(context.data.chapters[0].expanded, false);
  context.onHide();
});

test("story expands new sections but acknowledges only after explicit dismissal", async () => {
  const event = { id: "growth-1", project_id: "project-1", event_type: "story_structure_updated", status: "completed",
    summary_text: "新长出了一个小节", metadata_json: { new_chapter_ids: [], new_section_ids: ["section-2"] } };
  const { page, calls } = loadStoryPage({ current: snapshot({ automation_events: { items: [event] } }) });
  const context = pageContext(page);
  await context.onShow();
  await flushAsyncWork();
  assert.equal(context.data.chapters[1].expanded, true);
  assert.equal(context.data.chapters[1].sections[0].highlighted, true);
  assert.deepEqual(calls.acknowledgedEventIds, []);
  await context.dismissGrowthNotice();
  assert.deepEqual(calls.acknowledgedEventIds, ["growth-1"]);
  context.onHide();
});

test("story stops polling on hide, rejects old responses and resumes on reentry", async () => {
  let resolve;
  let current = snapshot({ progress: progress({ status: "processing" }) });
  const { page } = loadStoryPage({ getSnapshot: () => current ? Promise.resolve(current) : new Promise((done) => { resolve = done; }) });
  const context = pageContext(page);
  await context.onShow();
  assert.equal(context.progressTimer.pending, true);
  current = null;
  const pending = context.loadStory(true);
  context.onHide();
  assert.equal(context.progressTimer.pending, false);
  resolve(snapshot({ progress: progress({ status: "ready" }) }));
  await pending;
  assert.equal(context.data.progress.status, "processing");
  current = snapshot({ identity: { active_project: { id: "project-2" } }, progress: progress({ status: "failed", can_retry: true }) });
  await context.onShow();
  assert.equal(context.data.projectId, "project-2");
  assert.equal(context.data.progress.status, "failed");
  assert.equal(context.progressTimer.pending, false);
  context.onUnload();
});

test("story offers explicit retry and does not turn a refresh failure into job failure", async () => {
  let fail = false;
  let retries = 0;
  const current = snapshot({ progress: progress({ status: "failed", can_retry: true }) });
  const { page } = loadStoryPage({ getSnapshot: async () => { if (fail) throw Error("offline"); return current; },
    retry: async (id) => { assert.equal(id, "project-1"); retries++; current.progress = progress({ status: "processing" }); return current.progress; } });
  const context = pageContext(page);
  await context.onShow();
  await context.retryDraft();
  assert.equal(retries, 1);
  fail = true;
  await context.loadStory(true);
  assert.equal(context.data.progress.status, "processing");
  assert.equal(context.progressTimer.pending, true);
  assert.match(context.data.error, /进度暂未刷新/);
  context.onHide();
});

test("story reads book and chapter directly and retains editing destinations", async () => {
  const { page, calls } = loadStoryPage();
  const context = pageContext(page);
  const urls = [];
  global.wx.navigateTo = ({ url }) => urls.push(url);
  await context.onShow();
  context.openBook();
  context.readChapter({ currentTarget: { dataset: { id: "chapter-1" } } });
  context.readChapter({ currentTarget: { dataset: { id: "chapter-2" } } });
  context.openChapterManager();
  context.openChapter({ currentTarget: { dataset: { id: "chapter-1" } } });
  assert.deepEqual(urls, ["/pages/book-reader/index?projectId=project-1", "/pages/book-reader/index?projectId=project-1&chapterId=chapter-1",
    "/pages/chapter-workspace/index?projectId=project-1&chapterId=chapter-1"]);
  assert.equal(calls.book, 1);
  context.onHide();
});

test("story clears prior project data on auth loss or an empty account", async () => {
  let authFailure = true;
  const { page } = loadStoryPage({ getSnapshot: async () => {
    if (authFailure) throw { statusCode: 401 };
    return snapshot({ identity: { active_project: null } });
  } });
  const context = pageContext(page);
  await context.onShow();
  assert.equal(context.data.needsLogin, true);
  assert.equal(context.data.progress, null);
  authFailure = false;
  await context.loadStory();
  assert.equal(context.data.projectId, "");
  assert.equal(context.data.chapters.length, 0);
  context.onHide();
});
