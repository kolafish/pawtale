require("./register-ts");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const { flushAsyncWork, loadPageModule, mockModule, pageContext } = require("./page-harness");
const { storyViewData } = require("../components/story-tree/story-view-adapter.ts");

function progress(patch = {}) {
  return { project_id: "project-1", status: "ready", title: "可读草稿已更新", detail: "全书检查已完成",
    readable_chapter_count: 1, total_chapter_count: 1, material_count: 5, pending_material_count: 2,
    needs_user_material_count: 1, can_retry: false, ...patch };
}
function snapshot(patch = {}) {
  return {
    identity: { id: "user-1", email: "test@example.com", display_name: "作者", active_project_id: "project-1", active_project: { id: "project-1", title: "余仲尤的回忆录" } },
    progress: progress(), book_readable: true, book_char_count: 200, book_chapter_count: 1,
    book_issues: { review_count: 0, supplement_count: 0 }, book_manual_edit_preserved: false, review_issue_signature: "",
    updated_at: "2026-09-05T00:00:00Z",
    chapters: [1, 2].map((n) => ({ id: `chapter-${n}`, title: `章节 ${n}`, include_in_book: n === 1,
      material_count: 2, char_count: n === 1 ? 200 : 0, readable: n === 1,
      state: n === 1 ? "settled" : "muted", state_text: n === 1 ? "可阅读" : "暂不入书",
      image_count: 3, video_count: 1, issues: { review_count: 0, supplement_count: 0 },
      sections: [{ id: `section-${n}`, title: `小节 ${n}`, material_count: 1, image_count: 1, video_count: 2 }],
    })),
    automation_events: { items: [], total: 0, limit: 8 },
    unassigned_image_count: 2, unassigned_video_count: 1, growth_events: [], latest_transcript: null, ...patch,
  };
}
function loadStoryPage({ current = snapshot(), getSnapshot, retry, largeText = false, navigationTarget = null, bookOpened = false, acknowledge, storage = {}, likeNotice = null } = {}) {
  const calls = { acknowledgedEventIds: [], bookNavigations: 0, bookOpenedMarks: [], seenLikes: [] };
  mockModule("../stores/memoir-cache.ts", { writeMemoirIdentitySnapshot: () => {} });
  mockModule("../stores/memoir-read-cache.ts", { readCachedMemoirHome: () => ({status:"miss"}), readCachedMemoirIdentity: () => null, writeCachedMemoirHome: () => {} });
  mockModule("../services/home-launch-state.ts", {
    resolveHomeLaunch: () => "none", dismissHomeLaunch: () => {},
    homeBookOpened: () => bookOpened, markHomeBookOpened: (userId) => { calls.bookOpenedMarks.push(userId); },
  });
  mockModule("../services/voice-authorization.ts", { readVoiceAuthorizationConsent: () => true });
  mockModule("../services/accessibility-settings.ts", { readLargeTextEnabled: () => largeText, accessibilityPageClass: (base) => largeText ? `${base} large-text` : base });
  mockModule("../services/memoir-snapshot-api.ts", {
    getMemoirStorySnapshot: getSnapshot || (async () => current),
    retryMemoirDraftProgress: retry || (async () => progress({ status: "processing" })),
  });
  mockModule("../services/memoir-voice-session-api.ts", {
    acknowledgeMemoirAutomationEvent: async (_, id) => { calls.acknowledgedEventIds.push(id); if (acknowledge) await acknowledge(id); },
  });
  mockModule("../services/navigation.ts", {
    consumeHomeVoiceTarget: () => null,
    consumeNarrationAfterLogin: () => false,
    consumeHomeStoryTarget: () => { const target = navigationTarget; navigationTarget = null; return target; },
    CHAPTER_WORKSPACE_PAGE_URL: "/pages/chapter-workspace/index",
    isAuthError: (error) => [401, 403].includes(error?.statusCode),
    navigateToBook: () => { calls.bookNavigations += 1; }, navigateToLogin: () => {}, navigateToTranscripts: () => {},
  });
  mockModule("../services/share-likes.ts", {
    loadShareLikeNotice: async () => likeNotice,
    markShareLikeNoticeSeen: (...args) => calls.seenLikes.push(args),
  });
  global.wx = {
    getStorageSync: key => storage[key],
    setStorageSync: (key, value) => { storage[key] = value; },
    pageScrollTo: () => {},
    createSelectorQuery: () => ({select: () => ({boundingClientRect: callback => ({exec: () => callback({bottom:600})})})}),
  };
  return { ...loadPageModule("../pages/home/index.ts"), calls };
}

test("story maps server content into the hero, themes and book summary", () => {
  const view = storyViewData(snapshot());
  assert.equal(view.memoir.bookMetaText, "1 章 · 200 字");
  assert.equal(view.overview.title, "余仲尤的回忆录");
  assert.equal(view.overview.growthStage, 2);
  assert.equal(view.overview.growthImageSrc, "/assets/story-tree/tree-hero-stage-02-seedling.jpg");
  assert.deepEqual(view.overview.themeTags.map(tag => tag.title), ["章节 1", "章节 2"]);
  assert.deepEqual(view.overview.themeTags.map(tag => tag.readable), [true, false]);
  assert.equal(view.chapters[0].hasSections, true);
  assert.equal(view.overview.summaryText, "2 章 · 2 小节");
  assert.equal(view.overview.growthAnimating, false);
  assert.equal("growthText" in view.overview, false);
  assert.equal(view.chapters[0].storyCountText, "1 小节");
  assert.equal("statusText" in view.chapters[0], false);
  assert.equal(view.chapters[0].readable, true);

  const staleBookTitleView = storyViewData(snapshot({ book_title: "平生所忆那些事" }));
  assert.equal(staleBookTitleView.overview.title, "余仲尤的回忆录");

  const counted = snapshot();
  counted.chapters[0].sections.push({
    id: "section-extra", title: "另一个故事", material_count: 2, image_count: 0, video_count: 0,
  });
  const countedView = storyViewData(counted);
  assert.equal(countedView.overview.summaryText, "2 章 · 3 小节");
  assert.deepEqual(countedView.chapters.map(chapter => chapter.storyCountText), ["2 小节", "1 小节"]);
});

test("story distinguishes empty, organizing and section-less states", () => {
  const empty = storyViewData(snapshot({ chapters: [], book_readable: false, book_chapter_count: 0, book_char_count: 0, progress: null }));
  assert.equal(empty.overview.summaryText, "故事树还在等待第一段回忆");
  assert.equal(empty.overview.hasChapters, false);
  assert.equal(empty.memoir.bookMetaText, "正在整理");
  const noSections = storyViewData(snapshot({chapters:[{...snapshot().chapters[0], sections:[]}]}));
  assert.equal(noSections.chapters[0].hasSections, false);
  const organizing = storyViewData(snapshot({ chapters: [], book_readable: false, progress: progress({ material_count: 2 }) }));
  assert.equal(organizing.overview.summaryText, "回忆正在整理成故事");
});

test("story applies large text while refreshing the same project", async () => {
  const { page } = loadStoryPage({ largeText: true });
  const context = pageContext(page);
  await context.onShow();
  assert.match(context.data.homePageClass, /large-text/);
  assert.equal(context.data.largeTextEnabled, true);
  await context.loadHome();
  assert.equal(context.data.story.chapters[0].title, "章节 1");
  context.onHide();
});

test("refreshing the same home preserves its active voice entry; a project switch clears it", async () => {
  let current = snapshot();
  const { page } = loadStoryPage({ getSnapshot: async () => current });
  const context = pageContext(page);
  await context.loadHome();
  const voiceEntry = { voiceSessionId: "s1", projectId: "project-1" };
  context.setData({ voiceEntry });
  await context.loadHome(true);
  assert.equal(context.data.voiceEntry, voiceEntry);
  current = snapshot({ identity: { ...current.identity, active_project_id: "project-2", active_project: { id: "project-2", title: "另一本" } } });
  await context.loadHome(true);
  assert.equal(context.data.voiceEntry, null);
});

test("added chapters, sections and words, first readability and aggregated likes enter the shared queue", async () => {
  const { page, calls } = loadStoryPage({ current: snapshot({ growth_events: [
    { ...growth("later", "chapter_updated", "chapter-1"), created_at: "2026-09-05T01:00:00Z", book_char_count_delta: 386 },
    growth("earlier"),
  ] }), likeNotice: { latestAt: "2026-09-05T02:00:00Z", text: "三姑给书稿点了赞", avatars: [] } });
  const context = pageContext(page);
  try {
    await context.onShow();
    await flushAsyncWork();
    assert.equal(context.data.notice.text, "新增 1 小节");
    assert.equal(context.data.story.chapters[1].sections[0].highlighted, true);
    await context.dismissNotice();
    assert.deepEqual(calls.acknowledgedEventIds, ["earlier"]);
    assert.equal(context.data.notice.text, "新增 386 字");
    await context.dismissNotice();
    assert.equal(context.data.notice.kind, "readable");
    // A response fetched before acknowledgement must not reinsert an already read event.
    await context.loadHome(true);
    assert.deepEqual(calls.acknowledgedEventIds, ["earlier", "later"]);
    assert.equal(context.data.notice.text, "书稿已可阅读");
    await context.dismissNotice();
    assert.equal(context.data.notice.kind, "like");
    await context.dismissNotice();
    assert.deepEqual(calls.seenLikes, [["user-1", "project-1", "2026-09-05T02:00:00Z"]]);
    assert.equal(context.data.notice, null);
  } finally { context.onHide(); }
});

test("story stops polling on hide, rejects old responses and resumes on reentry", async () => {
  let resolve;
  let current = snapshot({ progress: progress({ status: "processing" }) });
  const { page } = loadStoryPage({ getSnapshot: () => current ? Promise.resolve(current) : new Promise((done) => { resolve = done; }) });
  const context = pageContext(page);
  await context.onShow();
  assert.equal(context.homeRefreshTimer.pending, true);
  current = null;
  const pending = context.loadHome(true);
  context.onHide();
  assert.equal(context.homeRefreshTimer.pending, false);
  resolve(snapshot({ progress: progress({ status: "ready" }) }));
  await pending;
  assert.equal(context.data.progress.status, "processing");
  current = snapshot({ identity: { id: "user-1", email: "test@example.com", display_name: "作者", active_project_id: "project-2", active_project: { id: "project-2" } }, progress: progress({ status: "failed", can_retry: true }) });
  await context.onShow();
  assert.equal(context.data.projectId, "project-2");
  assert.equal(context.data.progress.status, "failed");
  assert.equal(context.homeRefreshTimer.pending, true);
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
  await context.loadHome(true);
  assert.equal(context.data.progress.status, "processing");
  assert.equal(context.homeRefreshTimer.pending, true);
  assert.match(context.data.storyError, /进度暂未刷新/);
  context.onHide();
});

test("story opens the compact theme workspace", async () => {
  const { page } = loadStoryPage();
  const context = pageContext(page);
  const urls = [];
  global.wx.navigateTo = ({ url }) => urls.push(url);
  await context.onShow();
  context.setData({ loading: true });
  context.openChapter({ detail: { chapterId: "chapter-1" } });
  context.openChapter({ detail: { chapterId: "chapter-2" } });
  assert.deepEqual(urls, ["/pages/chapter-workspace/index?projectId=project-1&chapterId=chapter-1",
    "/pages/chapter-workspace/index?projectId=project-1&chapterId=chapter-2"]);
  context.onHide();
});

test("story tree always opens the book workspace", async () => {
  const { page, calls } = loadStoryPage();
  const context = pageContext(page);
  await context.onShow();
  assert.equal(context.data.showBookHint, true);
  context.openBook();
  assert.equal(calls.bookNavigations, 1);
  assert.deepEqual(calls.bookOpenedMarks, ["user-1"]);
  assert.equal(context.data.showBookHint, false);
  context.setData({ projectId: "" });
  context.openBook();
  assert.equal(calls.bookNavigations, 1);
  context.onHide();
});

test("story tree book hint stays hidden once the user has opened the book", async () => {
  const { page } = loadStoryPage({ bookOpened: true });
  const context = pageContext(page);
  await context.onShow();
  assert.equal(context.data.showBookHint, false);
  context.onHide();
});

test("story clears prior project data on auth loss or an empty account", async () => {
  let authFailure = false;
  let current = snapshot();
  const { page } = loadStoryPage({ getSnapshot: async () => {
    if (authFailure) throw { statusCode: 401 };
    return current;
  } });
  const context = pageContext(page);
  await context.onShow();
  assert.equal(context.data.story.chapters.length, 2);
  authFailure = true;
  await context.loadHome();
  assert.equal(context.data.needsLogin, true);
  assert.equal(context.data.progress, null);
  assert.equal(context.data.story.chapters.length, 0);
  assert.equal(context.data.story.overview.hasChapters, false);
  authFailure = false;
  await context.loadHome();
  assert.equal(context.data.story.chapters.length, 2);
  current = snapshot({ identity: { id: "user-1", email: "test@example.com", display_name: "作者", active_project_id: "", active_project: null }, chapters: [], progress: null, book_readable: false });
  await context.loadHome();
  assert.equal(context.data.projectId, "");
  assert.equal(context.data.story.chapters.length, 0);
  context.onHide();
});

test("home shows a later readable draft after an earlier structure event was acknowledged", async () => {
  let current = snapshot({ book_readable: false, progress: progress({ status: "processing" }), growth_events: [] });
  const { page, calls } = loadStoryPage({ getSnapshot: async () => current });
  const context = pageContext(page);
  await context.onShow();
  current = snapshot({ growth_events: [{ event_id: "draft-ready", summary_text: "这一章已经整理好了", created_at: "2026-09-05T01:00:00Z", book_char_count_delta: 0, changes: [{ kind: "chapter_updated", chapter_id: "chapter-1" }] }] });
  await context.loadHome(true);
  assert.equal(context.data.progress.status, "ready");
  assert.equal(context.data.story.chapters[0].growthLabel, "");
  assert.doesNotMatch(context.data.notice.text, /新长出/);
  assert.deepEqual(calls.acknowledgedEventIds, []);
  context.onHide();
});

function growth(id, kind = "section_added", chapterId = "chapter-2", sectionId = "section-2") {
  return { event_id: id, summary_text: "已整理", created_at: "2026-09-05T00:00:00Z", book_char_count_delta: 0, changes: [{ kind, chapter_id: chapterId, section_id: sectionId }] };
}

test("growth animates once after initial data and never scrolls during a refresh", async () => {
  let current = snapshot({ growth_events: [growth("old")] });
  const { page, calls } = loadStoryPage({ getSnapshot: async () => current });
  const context = pageContext(page);
  const scrolls = [];
  global.wx.pageScrollTo = options => scrolls.push(options);
  await context.onShow();
  assert.equal(context.data.storyError, "");
  assert.equal(context.data.story.chapters[1].sections[0].growthAnimating, false);
  current = snapshot({ growth_events: [growth("fresh", "section_updated"), growth("old")] });
  await context.loadHome(true);
  assert.equal(context.data.story.chapters[1].sections[0].growthAnimating, true);
  assert.equal(context.data.story.overview.growthAnimating, true);
  await context.loadHome(true);
  assert.equal(context.data.story.chapters[1].sections[0].growthAnimating, false);
  assert.equal(context.data.story.overview.growthAnimating, false);
  context.onHide();
  await context.onShow();
  assert.equal(context.data.story.chapters[1].sections[0].growthAnimating, false);
  assert.deepEqual(scrolls, []);
  assert.deepEqual(calls.acknowledgedEventIds, []);
  context.onHide();
});

test("updating an existing section leaves tree counts unchanged and deleted targets stay out", async () => {
  const { page } = loadStoryPage({ current: snapshot({growth_events:[growth("updated", "section_updated"), growth("deleted", "section_added", "missing", "gone")]}) });
  const context = pageContext(page);
  await context.onShow();
  assert.equal(context.data.story.chapters.length, 2);
  assert.equal(context.data.story.chapters[1].sections.length, 1);
  assert.equal(context.data.story.chapters[1].sections[0].growthLabel, "已补充");
  assert.equal(context.data.growthNotices.length, 0);
  context.onHide();
});

test("saved return scrolls once for its project; settings returns and foreign project intents do not", async () => {
  for (const projectId of ["project-1", "foreign"]) {
    const { page } = loadStoryPage({navigationTarget:{projectId,narrationSaved:true}});
    const context = pageContext(page), scrolls = [];
    global.wx.pageScrollTo = options => scrolls.push(options);
    await context.onShow();
    assert.equal(scrolls.length, projectId === "project-1" ? 1 : 0);
    context.onHide();
    await context.onShow();
    assert.equal(scrolls.length, projectId === "project-1" ? 1 : 0);
    context.onHide();
  }
});

test("continue narration appears beyond the narration panel and hides on return or account loss", async () => {
  let current = snapshot();
  const { page } = loadStoryPage({getSnapshot: async () => current});
  const context = pageContext(page);
  await context.onShow();
  context.onPageScroll({scrollTop:650});
  assert.equal(context.data.showContinueNarration, true);
  let starts = 0;
  context.startPrimaryVoiceChat = async () => starts++;
  await context.continueNarration();
  assert.equal(starts, 1);
  context.onPageScroll({scrollTop:300});
  assert.equal(context.data.showContinueNarration, false);
  context.onPageScroll({scrollTop:650});
  current = snapshot({identity:{id:"user-2",display_name:"另一位作者",email:"other@example.com",active_project_id:"",active_project:null},chapters:[],progress:null});
  await context.loadHome(true);
  assert.equal(context.data.showContinueNarration, false);
  context.onHide();
});

test("growth observation is isolated by user and project and reset on authentication loss", () => {
  const {HomeGrowthTracker} = require("../pages/home/home-story-state.ts");
  const tracker = new HomeGrowthTracker();
  assert.equal(tracker.observe("u1", "p1", []).size, 0);
  assert.equal(tracker.observe("u1", "p1", [growth("event")]).size, 1);
  assert.equal(tracker.observe("u1", "p2", [growth("event")]).size, 0);
  assert.equal(tracker.observe("u2", "p2", [growth("event")]).size, 0);
  tracker.reset();
  assert.equal(tracker.observe("u2", "p2", [growth("another")]).size, 0);
});


test("continue narration directs an unconsented reader to the authorization control", async () => {
  const {page} = loadStoryPage();
  const context = pageContext(page), scrolls = [];
  global.wx.pageScrollTo = options => scrolls.push(options);
  global.wx.showToast = () => {};
  await context.onShow();
  context.data.voiceAuthorizationAgreed = false;
  await context.continueNarration();
  assert.deepEqual(scrolls, [{selector:"#home-voice-authorization",duration:250}]);
  assert.equal(context.data.creatingVoice, false);
  assert.equal(context.data.voiceAuthorizationRequested, true);
  assert.equal(context.data.showVoiceAuthorizationPanel, true);
  assert.equal(context.data.error, "");
  context.onHide();
});


test("story keeps readable chapter drafts available while the assembled book awaits review", () => {
  const view = storyViewData(snapshot({
    book_readable: false,
    progress: progress({ status: "needs_input", readable_chapter_count: 0 }),
  }));
  assert.equal(view.chapters[0].readable, true);
  assert.equal(view.memoir.bookMetaText, "1 章 · 200 字");
});

const { homeStorySnapshotData } = require("../pages/home/home-story-state.ts");

test("home leaves candidate state inside the theme workspace", async () => {
  const current = snapshot({book_issues: {review_count: 1, supplement_count: 0}});
  Object.assign(current.chapters[0], {candidate_job_id: "candidate-1", state: "attention", state_text: "新稿待采纳"});
  const {page} = loadStoryPage({current});
  const context = pageContext(page);
  try {
    await context.onShow();
    assert.equal("candidateNoticeText" in context.data, false);
    assert.equal("statusText" in context.data.story.chapters[0], false);
  } finally { context.onHide(); }
});

test("home keeps chapter and book issue counts out of the story presentation", () => {
  const current = snapshot({
    book_issues: { review_count: 1, supplement_count: 0 },
    chapters: snapshot().chapters.map((chapter, i) => ({ ...chapter,
      issues: { review_count: i ? 0 : 2, supplement_count: i ? 1 : 0 } })),
  });
  const view = storyViewData(current);
  assert.equal(view.overview.summaryText, "2 章 · 2 小节");
  assert.equal("growthText" in view.overview, false);
  assert.equal("statusText" in view.chapters[0], false);
  const homeData = homeStorySnapshotData(current);
  assert.equal("reviewNoticeText" in homeData, false);
});

test("growth messages combine added chapters, sections and words while ignoring updates without growth", () => {
  const current = snapshot({ growth_events: [
    { event_id: "structure", summary_text: "故事树已更新", created_at: "2026-09-05T00:00:00Z", book_char_count_delta: 426,
      changes: [{ kind: "chapter_added", chapter_id: "chapter-1" }, { kind: "section_added", chapter_id: "chapter-2", section_id: "section-2" }] },
    { event_id: "draft", summary_text: "正文已更新", created_at: "2026-09-05T00:01:00Z", book_char_count_delta: 286,
      changes: [{ kind: "chapter_updated", chapter_id: "chapter-2" }] },
    { event_id: "book", summary_text: "书稿已更新", created_at: "2026-09-05T00:02:00Z", book_char_count_delta: 0,
      changes: [{ kind: "book_updated" }] },
    { event_id: "shorter-draft", summary_text: "正文已更新", created_at: "2026-09-05T00:03:00Z", book_char_count_delta: -80,
      changes: [{ kind: "chapter_updated", chapter_id: "chapter-1" }] },
  ], book_char_count: 12_400 });
  const view = storyViewData(current);
  const data = homeStorySnapshotData(current);
  assert.equal(view.memoir.bookMetaText, "1 章 · 约 1.2 万字");
  assert.deepEqual(data.growthNotices.map(notice => notice.text), ["新增 1 章，1 小节，426 字", "新增 286 字"]);
});

test("closed issues disappear despite an old needs_input job; five readable drafts are independent of one book decision", () => {
  const current = snapshot({
    chapters: Array.from({length: 5}, (_, i) => ({...snapshot().chapters[0], id: `chapter-${i}`})),
    progress: progress({status: "needs_input", readable_chapter_count: 0, needs_user_material_count: 0}),
    book_readable: false, book_chapter_count: 0, book_issues: {review_count: 1, supplement_count: 0},
  });
  assert.equal(storyViewData(current).overview.summaryText, "5 章 · 5 小节");
  assert.equal(storyViewData(current).memoir.bookMetaText, "5 章 · 200 字");
  current.book_readable = true;
  current.book_chapter_count = 3;
  assert.equal(storyViewData(current).memoir.bookMetaText, "3 章 · 200 字");
});

test("home story template exposes growth and counts without internal completion labels", () => {
  const template = fs.readFileSync(
    path.join(__dirname, "../components/story-tree/index.wxml"),
    "utf8",
  );
  assert.doesNotMatch(template, /hero-growth-copy|story-status|statusText/);
  assert.match(template, /growthLabel/);
  assert.match(template, /id="story-summary-toggle"/);
  assert.doesNotMatch(template, /树上的故事/);
});

test("home binds the shared notice queue and keeps the memoir card as the book entry", () => {
  const template = fs.readFileSync(path.join(__dirname, "../pages/home/index.wxml"), "utf8");
  assert.match(template, /bindtap="dismissNotice"/);
  assert.match(template, /bindtap="openBook"/);
  assert.doesNotMatch(template, /growthNoticeTitle|growthNoticeContext|viewStoryGrowth|dismissShareLikeNotice|home-story-progress/);
});

test("polling appends growth messages without replacing the current card, including bounded history", async () => {
  let current = snapshot({ progress: null, book_readable: false,
    chapters: snapshot().chapters.map(chapter => ({ ...chapter, readable: false })),
    growth_events: [{ ...growth("first", "chapter_updated", "chapter-1"), book_char_count_delta: 386 }] });
  const { page } = loadStoryPage({ getSnapshot: async () => current });
  const context = pageContext(page);
  try {
    await context.onShow();
    current = { ...current, growth_events: [{ ...growth("second"), created_at: "2026-09-05T01:00:00Z" }] };
    await context.loadHome(true);
    assert.equal(context.data.notice.eventId, "first");
    assert.deepEqual(context.data.notices.map(item => item.eventId), ["first", "second"]);
    await context.dismissNotice();
    assert.equal(context.data.notice.eventId, "second");
  } finally { context.onHide(); }
});

test("a failed acknowledgement keeps the current notification available for an explicit retry", async () => {
  let shouldFail = true;
  const { page } = loadStoryPage({ current: snapshot({ progress: null, growth_events: [growth("first"), growth("second")] }),
    acknowledge: async () => { if (shouldFail) throw Error("offline"); } });
  const context = pageContext(page);
  try {
    await context.onShow();
    await context.dismissNotice();
    assert.equal(context.data.notice.eventId, "first");
    assert.equal(context.data.dismissingGrowth, false);
    assert.equal(context.data.storyError, "offline");
    shouldFail = false;
    await context.dismissNotice();
    assert.equal(context.data.notice.eventId, "second");
    assert.equal(context.data.storyError, "");
  } finally { context.onHide(); }
});

test("reused editorial events stay silent before, during and after being requeued", async () => {
  const editorial = { ...growth("editorial-copy"), changes: [{ kind: "book_updated" }] };
  let current = snapshot({ growth_events: [editorial] });
  const { page, calls } = loadStoryPage({ getSnapshot: async () => current });
  const context = pageContext(page);
  try {
    await context.onShow();
    await context.dismissNotice(); // First readability only.
    current = snapshot({ progress: progress({ status: "processing" }), growth_events: [growth("other-update")] });
    await context.loadHome(true);
    assert.equal(context.data.notice.eventId, "other-update");
    await context.dismissNotice();
    assert.equal(context.data.notice, null);
    current = snapshot({ growth_events: [editorial] });
    await context.loadHome(true);
    assert.equal(context.data.notice, null);
    assert.deepEqual(calls.acknowledgedEventIds, ["other-update"]);
  } finally { context.onHide(); }
});

test("first partial readability stays read across full readiness, new versions and page recreation, scoped by user and memoir", async () => {
  const storage = {};
  let current = snapshot({ progress: progress({ status: "partial_ready" }) });
  const first = pageContext(loadStoryPage({ storage, getSnapshot: async () => current }).page);
  await first.onShow();
  assert.equal(first.data.notice.kind, "readable");
  assert.equal(first.data.notice.text, "已有部分书稿可阅读");
  await first.dismissNotice();
  assert.equal(first.data.notice, null);
  first.onUnload();
  current = snapshot({ updated_at: "2026-09-06T00:00:00Z" });
  const second = pageContext(loadStoryPage({ storage, getSnapshot: async () => current }).page);
  try {
    await second.onShow();
    assert.equal(second.data.notice, null);
    for (const status of ["partial_ready", "ready"]) {
      current = snapshot({ updated_at: "2026-09-07T00:00:00Z", progress: progress({ status }) });
      await second.loadHome(true);
      assert.equal(second.data.notice, null);
    }
    current = snapshot({ identity: { ...current.identity, active_project_id: "project-2", active_project: { id: "project-2" } } });
    await second.loadHome(true);
    assert.equal(second.data.notice.kind, "readable");
    await second.dismissNotice();
    current = snapshot();
    await second.loadHome(true);
    assert.equal(second.data.notice, null);
    current = snapshot({ identity: { ...current.identity, id: "user-2" } });
    await second.loadHome(true);
    assert.equal(second.data.notice.kind, "readable");
  } finally { second.onHide(); }
});

test("first readability requires a readable draft, independently of background processing or failure", async () => {
  let current = snapshot({ book_readable: false,
    chapters: snapshot().chapters.map(chapter => ({ ...chapter, readable: false })) });
  const { page } = loadStoryPage({ getSnapshot: async () => current });
  const context = pageContext(page);
  try {
    await context.onShow();
    assert.equal(context.data.notice, null);
    current = snapshot({ progress: progress({ status: "processing" }), book_readable: false });
    await context.loadHome(true);
    assert.equal(context.data.notice.kind, "readable");
    await context.dismissNotice();
    current = snapshot({ progress: progress({ status: "failed", can_retry: true }) });
    await context.loadHome(true);
    assert.equal(context.data.notice.kind, "progress");
    assert.equal(context.data.notice.action, "retry");
  } finally { context.onHide(); }
});

test("an acknowledgement completed after an account switch cannot remove the new account's message", async () => {
  let current = snapshot({ progress: null, growth_events: [growth("same-event")] });
  let finishAcknowledgement;
  const { page } = loadStoryPage({ getSnapshot: async () => current,
    acknowledge: () => new Promise(resolve => { finishAcknowledgement = resolve; }) });
  const context = pageContext(page);
  try {
    await context.onShow();
    const pending = context.dismissNotice();
    current = snapshot({ ...current, identity: { ...current.identity, id: "user-2" } });
    await context.loadHome(true);
    finishAcknowledgement();
    await pending;
    assert.equal(context.data.userId, "user-2");
    assert.equal(context.data.notice.eventId, "same-event");
    assert.equal(context.data.dismissingGrowth, false);
  } finally { context.onHide(); }
});

test("normal narration, saved returns, updates without word growth and optional supplements stay silent after first readability", async () => {
  const updates = [
    { ...growth("section-edit", "section_updated"), book_char_count_delta: 0 },
    { ...growth("draft", "chapter_updated"), book_char_count_delta: 0 },
    { ...growth("shorter-draft", "chapter_updated"), book_char_count_delta: -80 },
    { ...growth("book"), changes: [{ kind: "book_updated" }] },
    { ...growth("copy"), changes: [{ kind: "book_updated" }] },
    { ...growth("no-additions"), summary_text: "讲述已整理", changes: [] },
  ];
  let current = snapshot();
  const { page, calls } = loadStoryPage({ getSnapshot: async () => current,
    navigationTarget: { projectId: "project-1", narrationSaved: true } });
  const context = pageContext(page);
  try {
    await context.onShow();
    assert.deepEqual(context.data.notices.map(notice => notice.kind), ["readable"]);
    await context.dismissNotice();
    for (const [status, stage] of [["idle", "queued"], ["processing", "organizing"], ["processing", "writing_chapters"],
      ["processing", "writing_copy"], ["processing", "reviewing_book"], ["no_change", "needs_attention"],
      ["partial_ready", "partial_ready"], ["ready", "ready"]]) {
      current = snapshot({ growth_events: updates, updated_at: `${status}/${stage}`,
        progress: progress({ status, stage, can_retry: status === "no_change", needs_user_material_count: 3 }),
        book_issues: { review_count: 0, supplement_count: 3 } });
      await context.loadHome(true);
      assert.equal(context.data.notice, null, `${status}/${stage} must stay silent`);
    }
    assert.deepEqual(calls.acknowledgedEventIds, []);
    assert.deepEqual(context.growthTracker.pending([]), []);
  } finally { context.onHide(); }
});

test("failures and required confirmation stay visible without re-notifying for stage or revision changes", async () => {
  let current = snapshot({ progress: progress({ status: "failed", can_retry: true }) });
  const { page } = loadStoryPage({ getSnapshot: async () => current });
  const context = pageContext(page);
  try {
    await context.onShow();
    assert.equal(context.data.notice.action, "retry");
    await context.dismissNotice();
    assert.equal(context.data.notice.kind, "readable");
    await context.dismissNotice();
    current = snapshot({ updated_at: "2026-09-06T00:00:00Z", progress: progress({ status: "failed", stage: "different-stage", can_retry: true }) });
    await context.loadHome(true);
    assert.equal(context.data.notice, null);
    current = snapshot({ progress: progress({ status: "needs_input", needs_user_material_count: 3 }), review_issue_signature: "issue-a" });
    await context.loadHome(true);
    assert.equal(context.data.notice.action, "review");
    await context.dismissNotice();
    assert.equal(context.data.notice, null);
    // A genuinely different required review remains notifyable while needs_input persists.
    current = snapshot({ progress: progress({ status: "needs_input" }), review_issue_signature: "issue-b", book_issues: { review_count: 1, supplement_count: 0 } });
    await context.loadHome(true);
    assert.equal(context.data.notice.action, "review");
    await context.dismissNotice();
    current = snapshot({ progress: progress({ status: "processing" }), review_issue_signature: "issue-b", book_issues: { review_count: 1, supplement_count: 0 } });
    await context.loadHome(true);
    assert.equal(context.data.notice, null);
    current = snapshot({ progress: progress({ status: "needs_input" }), review_issue_signature: "issue-b", book_issues: { review_count: 1, supplement_count: 0 } });
    await context.loadHome(true);
    assert.equal(context.data.notice, null, "background processing must not make the same review unread again");
  } finally { context.onHide(); }
});


test("a recovered failure can notify again with the same message after page recreation", async () => {
  const storage = {};
  const failure = snapshot({ progress: progress({ status: "failed", can_retry: true,
    detail: "成书文案生成失败，可以继续整理重试。" }) });
  let current = failure;
  const first = pageContext(loadStoryPage({ storage, getSnapshot: async () => current }).page);
  try {
    await first.onShow();
    assert.equal(first.data.notice.action, "retry");
    await first.dismissNotice();
    await first.dismissNotice(); // First readability.
    current = snapshot({ progress: progress({ status: "processing" }) });
    await first.loadHome(true);
    current = failure;
    await first.loadHome(true);
    assert.equal(first.data.notice, null, "processing alone does not prove the old failure recovered");
    current = snapshot({ progress: progress({ status: "ready" }) });
    await first.loadHome(true);
    assert.equal(first.data.notice, null, "recovery itself stays silent");
  } finally { first.onUnload(); }

  const second = pageContext(loadStoryPage({ storage, current: failure }).page);
  try {
    await second.onShow();
    assert.equal(second.data.notice?.action, "retry", "a new failure must not inherit a recovered failure's read state");
    await second.dismissNotice();
    await second.loadHome(true);
    assert.equal(second.data.notice, null, "repeated snapshots of the new failure stay read");
  } finally { second.onHide(); }
});

test("replacing a required issue with another in the same scope and count notifies after page recreation", async () => {
  const storage = {};
  const sameScope = snapshot({ progress: progress({ status: "needs_input" }),
    book_issues: { review_count: 1, supplement_count: 0 } });
  const first = pageContext(loadStoryPage({ storage, current: { ...sameScope, review_issue_signature: "old-issue" } }).page);
  await first.onShow();
  assert.equal(first.data.notice.action, "review");
  await first.dismissNotice();
  await first.dismissNotice(); // First readability.
  assert.equal(first.data.notice, null);
  first.onUnload();
  const second = pageContext(loadStoryPage({ storage, current: { ...sameScope, review_issue_signature: "replacement-issue" } }).page);
  try {
    await second.onShow();
    assert.equal(second.data.notice?.action, "review", "a different issue must not inherit the old issue's read state");
  } finally { second.onHide(); }
});
