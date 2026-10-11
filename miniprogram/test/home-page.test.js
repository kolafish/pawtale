require("./register-ts");

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const {
  mockMemoirApiModules,
  pageContext,
  flushAsyncWork,
  loadPageModule,
  mockModule,
  clearModule,
  unmockedAsync,
} = require("./page-harness");
const {
  memoirHomePrompt,
  memoirHomepageTitle,
  memoirStoryTitle,
} = require("../services/memoir-display-copy.ts");
const { userDisplayName } = require("../services/user-display-name.ts");

function deferred() {
  let resolve;
  const promise = new Promise((promiseResolve) => {
    resolve = promiseResolve;
  });
  return { promise, resolve };
}

function loadHomePage({
  authExports = {},
  voiceApiExports = {},
  voiceAuthorizationExports = {},
  launchStateExports = {},
  accessibilityExports = {},
  cacheExports = {},
  projectExports = {},
} = {}) {
  const getMemoirIdentity = authExports.getMemoirIdentity || unmockedAsync("getMemoirIdentity");
  const listMemoirVoiceTranscripts = voiceApiExports.listMemoirVoiceTranscripts
    || (async () => ({ items: [], total: 0, limit: 1, offset: 0 }));
  mockModule("../services/auth.ts", {
    getMemoirIdentity,
    logoutBackendSession: async () => {},
    ...authExports,
  });
  mockMemoirApiModules({
    createMemoirVoiceSession: unmockedAsync("createMemoirVoiceSession"),
    listMemoirAutomationEvents: async () => ({ items: [], total: 0, limit: 8 }),
    listMemoirVoiceTranscripts,
    getMemoirShareVideoCandidate: async () => ({ candidate: null }),
    listMemoirShareVideos: async () => ({ items: [], active_jobs: [], failed_jobs: [], total: 0, limit: 1, offset: 0 }),
    downloadMemoirShareVideoThumbnail: async () => "/tmp/share-video-thumbnail.jpg",
    getMemoirStorySnapshot: async () => {
      const identity = await getMemoirIdentity();
      const latest = identity.active_project_id
        ? await listMemoirVoiceTranscripts({
          projectId: identity.active_project_id,
          limit: 1,
        })
        : { items: [] };
      return {
        server_revision: 1,
        chapters: [], progress: null, growth_events: [], automation_events: { items: [], total: 0, limit: 8 },
        book_readable: false, book_char_count: 0, book_chapter_count: 0,
        book_issues: { review_count: 0, supplement_count: 0 }, book_manual_edit_preserved: false,
        updated_at: "", unassigned_image_count: 0, unassigned_video_count: 0,
        identity,
        latest_transcript: latest.items[0] || null,
      };
    },
    ...voiceApiExports,
  });
  mockModule("../services/memoir.ts", {
    createMemoirProject: unmockedAsync("createMemoirProject"),
    ...projectExports,
  });
  mockModule("../stores/memoir-cache.ts", { writeMemoirIdentitySnapshot: () => {} });
  mockModule("../stores/memoir-read-cache.ts", {
    readCachedMemoirIdentity: () => null,
    readCachedMemoirHome: () => ({ status: "miss" }),
    writeCachedMemoirHome: () => {},
    ...cacheExports,
  });
  mockModule("../services/voice-authorization.ts", {
    readVoiceAuthorizationConsent: () => true,
    saveVoiceAuthorizationConsent: (agreed = true) => ({
      agreed,
      version: "test",
      agreedAt: "2026-06-18T00:00:00.000Z",
    }),
    syncVoiceAuthorizationConsent: async () => ({ id: "consent-1" }),
    VOICE_AUTHORIZATION_PAGE_URL: "/pages/voice-authorization/index",
    ...voiceAuthorizationExports,
  });
  mockModule("../services/home-launch-state.ts", {
    dismissHomeLaunch: () => {},
    homeBookOpened: () => false,
    markHomeBookOpened: () => {},
    resolveHomeLaunch: () => "none",
    ...launchStateExports,
  });
  mockModule("../services/accessibility-settings.ts", {
    readLargeTextEnabled: () => false,
    ...accessibilityExports,
  });

  clearModule("../services/story-growth-notice.ts");
  const loaded = loadPageModule("../pages/home/index.ts");
  loaded.page.homeVisible = true;
  return loaded;
}

test("home starts once and defers mounting a session created while hidden", async () => {
  const creation = deferred();
  let creates = 0;
  const { page } = loadHomePage({ voiceApiExports: {
    createMemoirVoiceSession: () => { creates += 1; return creation.promise; },
  } });
  global.wx = { pageScrollTo() {} };
  const context = pageContext(page, { userId: "u1", projectId: "p1", hasProject: true, voiceAuthorizationAgreed: true });
  const pending = context.startPrimaryVoiceChat();
  await flushAsyncWork();
  await context.startPrimaryVoiceChat();
  assert.equal(creates, 1);
  context.onHide();
  creation.resolve({ session: { id: "s1" } });
  await pending;
  assert.equal(context.data.voiceEntry, null);
  context.homeVisible = true;
  context.openPendingVoiceSession();
  assert.equal(context.data.voiceEntry.voiceSessionId, "s1");
  await context.startPrimaryVoiceChat();
  assert.equal(creates, 1);
});

test("home ignores consent and session responses for an account or project that changed", async () => {
  for (const duringCreation of [false, true]) {
    const pending = deferred();
    let creates = 0;
    const { page } = loadHomePage({
      voiceAuthorizationExports: { syncVoiceAuthorizationConsent: () => duringCreation ? Promise.resolve() : pending.promise },
      voiceApiExports: { createMemoirVoiceSession: () => { creates += 1; return pending.promise; } },
    });
    const context = pageContext(page, { userId: "u1", projectId: "p1", hasProject: true, voiceAuthorizationAgreed: true });
    const starting = context.startPrimaryVoiceChat();
    await flushAsyncWork();
    context.setData({ userId: "u2", projectId: "p2" });
    pending.resolve({ session: { id: "old-session" } });
    await starting;
    assert.equal(creates, duringCreation ? 1 : 0);
    assert.equal(context.data.voiceEntry, null);
    assert.equal(context.voiceNavigationTarget, null);
  }
});

test("home closes only the current confirmed session, then refreshes the story in place", async () => {
  const { page } = loadHomePage();
  const context = pageContext(page, { userId: "u1", projectId: "p1", voiceEntry: { voiceSessionId: "s2" } });
  let refreshes = 0, storyViews = 0;
  context.measureNarration = () => {};
  context.loadHome = async () => { refreshes += 1; };
  context.openStoryProgress = () => { storyViews += 1; };
  await context.exitVoiceSession({ detail: { destination: "story", projectId: "p1", voiceSessionId: "s1", narrationSaved: true, finished: true } });
  assert.equal(context.data.voiceEntry.voiceSessionId, "s2");
  await context.exitVoiceSession({ detail: { destination: "story", projectId: "p1", voiceSessionId: "s2", narrationSaved: false } });
  assert.equal(storyViews, 1);
  assert.equal(context.data.voiceEntry.voiceSessionId, "s2");
  await context.exitVoiceSession({ detail: { destination: "story", projectId: "p1", voiceSessionId: "s2", narrationSaved: false, finished: true } });
  assert.equal(context.data.voiceEntry, null, "an empty but confirmed session can also close");
  assert.equal(refreshes, 1);
  assert.deepEqual(context.storyNavigationTarget, { projectId: "p1", narrationSaved: false });
});

test("mini program opens on the home page without a tab bar", () => {
  const appJson = JSON.parse(fs.readFileSync(path.join(__dirname, "../app.json"), "utf8"));

  assert.equal(appJson.pages[0], "pages/home/index");
});

test("home exposes mini program share entry points", () => {
  const pageWxml = fs.readFileSync(path.join(__dirname, "../pages/home/index.wxml"), "utf8");
  const { page } = loadHomePage();

  assert.match(pageWxml, /open-type="share"/);
  assert.deepEqual(page.onShareAppMessage(), {
    title: "老余口述，把家里的故事慢慢讲下来",
    path: "/pages/home/index?from=share_home",
  });
  assert.deepEqual(page.onShareTimeline(), {
    title: "老余口述，把家里的故事慢慢讲下来",
    query: "from=share_home",
  });
});

test("home recommendation opens the editor immediately without creating or accepting a video", async () => {
  const candidate = {
    id: "candidate-1", project_id: "p1", voice_session_id: "session-1",
    utterance_id: "utterance-1", selected_text: "父亲把围巾围在我脖子上。",
    duration_ms: 30000, status: "ready", video_job_id: null,
  };
  const created = [];
  const navigations = [];
  const { page } = loadHomePage({ voiceApiExports: {
    getMemoirShareVideoCandidate: async () => ({ candidate }),
    createMemoirShareVideoCandidateJob: async (...args) => {
      created.push(args);
      return new Promise(() => {});
    },
    dismissMemoirShareVideoCandidate: async () => ({ ...candidate, status: "dismissed" }),
  } });
  const context = pageContext(page, { userId: "u1", projectId: "p1", hasProject: true });
  global.wx = { navigateTo({ url }) { navigations.push(url); } };

  await context.refreshShareCandidate("p1");
  assert.equal(context.data.shareCandidate.id, "candidate-1");
  assert.equal(context.data.shareCandidateDurationText, "30 秒");
  context.openShareCandidateEditor();
  assert.deepEqual(created, []);
  assert.equal(context.data.shareCandidate.status, "ready");
  assert.deepEqual(navigations, ["/pages/transcript-media/index?id=utterance-1&candidateId=candidate-1"]);

  context.setData({ shareCandidate: { ...candidate, status: "accepted", video_job_id: "video-1" } });
  context.openShareCandidateVideo();
  assert.equal(navigations.length, 2);
  assert.equal(navigations[1], "/pages/transcript-media/index?id=utterance-1&jobId=video-1");
  await context.dismissShareCandidate();
  assert.equal(context.data.shareCandidate, null);
});

test("home replaces a cached failed recommendation job with the latest remake and refreshes it", async () => {
  const candidate = {
    id: "candidate-1", project_id: "p1", utterance_id: "utterance-1",
    status: "accepted", video_job_id: "remade-video",
  };
  let status = "running";
  const calls = [];
  const navigations = [];
  const thumbnails = [];
  const { page } = loadHomePage({ voiceApiExports: {
    getMemoirShareVideoCandidate: async () => ({ candidate }),
    getMemoirShareVideoJob: async (id) => {
      calls.push(id);
      return { id, status, title: "重制的往事" };
    },
    downloadMemoirShareVideoThumbnail: async (id) => {
      thumbnails.push(id);
      return "/tmp/remade-thumbnail.jpg";
    },
  } });
  const context = pageContext(page, {
    projectId: "p1", hasProject: true,
    shareCandidate: { ...candidate, video_job_id: "old-failed-video" },
    shareCandidateJob: { id: "old-failed-video", status: "failed" },
  });
  global.wx = { navigateTo({ url }) { navigations.push(url); } };
  await context.refreshShareCandidate("p1");
  assert.equal(context.data.shareCandidateJob.id, "remade-video");
  assert.equal(context.data.shareCandidateJob.status, "running");
  context.openShareCandidateVideo();
  assert.deepEqual(navigations, ["/pages/transcript-media/index?id=utterance-1&jobId=remade-video"]);
  status = "succeeded";
  await context.refreshShareCandidate("p1");
  assert.equal(context.data.shareCandidateJob.status, "succeeded");
  assert.equal(context.data.shareCandidateThumbnailPath, "/tmp/remade-thumbnail.jpg");
  await context.refreshShareCandidate("p1");
  assert.deepEqual(calls, ["remade-video", "remade-video"]);
  assert.deepEqual(thumbnails, ["remade-video"]);
});

test("home editor entry keeps voice and dismiss guards", () => {
  const pageWxml = fs.readFileSync(path.join(__dirname, "../pages/home/index.wxml"), "utf8");
  // One entry: the recommendation opens the same settings as a hand-picked clip instead of starting at once.
  assert.doesNotMatch(pageWxml, /data-create|>调整</);
  const created = [];
  const navigations = [];
  const candidate = {
    id: "candidate-1", project_id: "p1", utterance_id: "utterance-1",
    status: "ready", start_char: 0, end_char: 13,
  };
  const { page } = loadHomePage({ voiceApiExports: {
    createMemoirShareVideoCandidateJob: async (...args) => { created.push(args); },
  } });
  global.wx = { navigateTo({ url }) { navigations.push(url); } };
  const context = pageContext(page, { projectId: "p1", hasProject: true, shareCandidate: candidate });
  context.openShareCandidateEditor();
  assert.equal(context.data.shareCandidate.status, "ready");
  context.setData({ voiceEntry: { voiceSessionId: "session-1" } });
  context.openShareCandidateEditor();
  context.setData({ voiceEntry: null, shareCandidateDismissing: true });
  context.openShareCandidateEditor();
  assert.deepEqual(created, []);
  assert.deepEqual(navigations, ["/pages/transcript-media/index?id=utterance-1&candidateId=candidate-1"]);
});

test("home keeps completed videos in the library after handling the latest suggestion", async () => {
  const pageWxml = fs.readFileSync(path.join(__dirname, "../pages/home/index.wxml"), "utf8");
  const latest = {
    id: "video-2", utterance_id: "utterance-2", title: "外婆家的夏天",
    selected_text: "每年夏天，我都会去外婆家。", duration_ms: 28000,
    created_at: "2026-09-24T08:00:00", completed_at: "2026-09-24T08:01:00",
  };
  const calls = [];
  const navigations = [];
  const { page } = loadHomePage({ voiceApiExports: {
    listMemoirShareVideos: async (...args) => {
      calls.push(args);
      return { items: [latest], active_jobs: [], failed_jobs: [], total: 5, limit: 1, offset: 0 };
    },
  } });
  const context = pageContext(page, { userId: "u1", projectId: "p1", hasProject: true });
  global.wx = { navigateTo({ url }) { navigations.push(url); } };

  await context.refreshShareVideos("p1");
  assert.deepEqual(calls, [["p1", { limit: 1, offset: 0 }]]);
  assert.equal(context.data.shareCandidate, null);
  assert.equal(context.data.shareVideoTotal, 5);
  // Without a suggestion the whole card opens the library; it no longer carries a separate clip picker.
  context.openShareCard();
  assert.deepEqual(navigations, [
    "/pages/share-videos/index?projectId=p1",
  ]);
  assert.match(pageWxml, /class="home-share-candidate"[^>]*bindtap="openShareCard"/);
  assert.doesNotMatch(pageWxml, /挑一段做视频/);
  // A suggestion keeps its own buttons, so taps that bubble up to the card do nothing more.
  context.setData({ shareCandidate: { id: "c1", status: "ready" } });
  context.openShareCard();
  assert.equal(navigations.length, 1);
});

test("home exposes the video progress library while the first manually selected story is still generating", async () => {
  const navigations = [];
  const { page } = loadHomePage({ voiceApiExports: {
    listMemoirShareVideos: async () => ({
      items: [], active_jobs: [{ id: "first-video", status: "running" }], failed_jobs: [], total: 0, limit: 1, offset: 0,
    }),
  } });
  const context = pageContext(page, { userId: "u1", projectId: "p1", hasProject: true });
  global.wx = { navigateTo({ url }) { navigations.push(url); } };
  await context.refreshShareVideos("p1");
  assert.equal(context.data.shareCandidate, null);
  assert.equal(context.data.shareVideoTotal, 0);
  assert.equal(context.data.shareVideoActiveCount, 1);
  context.openShareCard();
  assert.deepEqual(navigations, ["/pages/share-videos/index?projectId=p1"]);
  const wxml = fs.readFileSync(path.join(__dirname, "../pages/home/index.wxml"), "utf8");
  assert.match(wxml, /wx:if="{{[^"\n]*shareVideoActiveCount[^"\n]*}}"\s+class="home-share-candidate"/);
});

test("home keeps the video library reachable when all manually selected video tasks have failed", async () => {
  const navigations = [];
  const { page } = loadHomePage({ voiceApiExports: {
    listMemoirShareVideos: async () => ({
      items: [], active_jobs: [], failed_jobs: [{ id: "first-video", status: "failed" }],
      total: 0, limit: 1, offset: 0,
    }),
  } });
  const context = pageContext(page, { userId: "u1", projectId: "p1", hasProject: true });
  global.wx = { navigateTo({ url }) { navigations.push(url); } };
  await context.refreshShareVideos("p1");
  assert.equal(context.data.shareCandidate, null);
  assert.equal(context.data.shareVideoTotal, 0);
  assert.equal(context.data.shareVideoActiveCount, 0);
  assert.equal(context.data.shareVideoFailedCount, 1);
  context.openShareCard();
  assert.deepEqual(navigations, ["/pages/share-videos/index?projectId=p1"]);
  const wxml = fs.readFileSync(path.join(__dirname, "../pages/home/index.wxml"), "utf8");
  assert.match(wxml, /wx:if="{{[^"\n]*shareVideoFailedCount[^"\n]*}}"\s+class="home-share-candidate"/);
});

test("only a successful completed-video preview dismisses the latest suggestion", async () => {
  for (const [status, navigateSuccessfully] of [["succeeded", true], ["succeeded", false], ["running", true]]) {
    const candidate = { id: "candidate-1", project_id: "p1", utterance_id: "utterance-1", status: "accepted", video_job_id: "video-1" };
    const dismissals = [];
    const { page } = loadHomePage({ voiceApiExports: {
      dismissMemoirShareVideoCandidate: async (...args) => { dismissals.push(args); },
    } });
    const context = pageContext(page, {
      projectId: "p1", shareCandidate: candidate,
      shareCandidateJob: { id: "video-1", status }, shareVideoTotal: 6,
      shareCandidateThumbnailPath: "/tmp/video-1.jpg",
    });
    global.wx = { navigateTo({ url, success }) {
      assert.equal(url, "/pages/transcript-media/index?id=utterance-1&jobId=video-1");
      if (navigateSuccessfully) success();
    } };
    context.openShareCandidateVideo();
    await flushAsyncWork();
    assert.equal(dismissals.length, status === "succeeded" && navigateSuccessfully ? 1 : 0);
    if (dismissals.length) {
      assert.deepEqual(dismissals[0], ["p1", "candidate-1"]);
      assert.equal(context.data.shareCandidate, null);
      assert.equal(context.data.shareCandidateThumbnailPath, "");
    } else assert.equal(context.data.shareCandidate.id, "candidate-1");
  }
});

test("home discards a thumbnail downloaded after switching projects", async () => {
  const thumbnail = deferred();
  const candidate = { id: "candidate-1", status: "accepted", video_job_id: "video-1" };
  const { page } = loadHomePage({ voiceApiExports: {
    getMemoirShareVideoCandidate: async () => ({ candidate }),
    getMemoirShareVideoJob: async () => ({ id: "video-1", status: "succeeded" }),
    downloadMemoirShareVideoThumbnail: () => thumbnail.promise,
  } });
  const context = pageContext(page, { projectId: "p1" });
  const refresh = context.refreshShareCandidate("p1");
  await flushAsyncWork();
  context.setData({ projectId: "p2" });
  thumbnail.resolve("/tmp/old-project.jpg");
  await refresh;
  assert.equal(context.data.shareCandidateThumbnailPath, "");
});

test("home ignores a recommendation returned after the project changes", async () => {
  const pending = deferred();
  const { page } = loadHomePage({ voiceApiExports: {
    getMemoirShareVideoCandidate: () => pending.promise,
  } });
  const context = pageContext(page, { userId: "u1", projectId: "p1", hasProject: true });
  const loading = context.refreshShareCandidate("p1");
  context.setData({ projectId: "p2" });
  pending.resolve({ candidate: { id: "old-candidate", status: "ready" } });
  await loading;
  assert.equal(context.data.shareCandidate, null);
});

test("home combines new-story and resume actions without mixing their routes", () => {
  const pageWxml = fs.readFileSync(path.join(__dirname, "../pages/home/index.wxml"), "utf8");

  assert.match(pageWxml, /id="primary-voice-action"[\s\S]*?bindtap="startPrimaryVoiceChat"/);
  assert.match(pageWxml, /id="resume-story-action"[\s\S]*?bindtap="resumeLastStory"/);
  assert.match(pageWxml, /wx:if="\{\{showNarrationChoiceCard\}\}"/);
  assert.match(pageWxml, /wx:if="\{\{narrationSaved && !growthNoticeText\}\}"/);
  assert.doesNotMatch(pageWxml, /resume-card|接着上次说/);
  assert.match(pageWxml, /wx:if="\{\{headerSubtitleText\}\}"/);
  assert.match(pageWxml, /<voice-session[\s\S]*?bindexit="exitVoiceSession"/);
  assert.doesNotMatch(pageWxml, /bindreturnhome|voiceSessionMinimized/);
});

test("home keeps interview-engine notices out of the story overview", () => {
  const pageWxml = fs.readFileSync(path.join(__dirname, "../pages/home/index.wxml"), "utf8");

  assert.match(pageWxml, /<story-tree/);
  assert.match(pageWxml, /bind:openbook="openBook"/);
  assert.doesNotMatch(pageWxml, /reviewNoticeText|candidateNoticeText/);
});

test("home only surfaces failed draft progress that the reader can act on", () => {
  const pageWxml = fs.readFileSync(path.join(__dirname, "../pages/home/index.wxml"), "utf8");

  assert.match(pageWxml, /wx:if="\{\{progress && progress\.status === 'failed'\}\}"/);
  assert.match(pageWxml, /bindtap="retryDraft"/);
});

test("home supports a local large text mode without changing the default class", () => {
  const loaded = loadHomePage({
    accessibilityExports: {
      readLargeTextEnabled: () => true,
    },
  });
  const { homeViewPatch } = loaded.mod;
  const { page } = loaded;
  const context = {
    ...page,
    data: { ...page.data },
    setData(patch, callback) {
      this.data = { ...this.data, ...patch };
      callback?.();
    },
  };

  page.refreshAccessibilitySettings.call(context);
  assert.equal(context.data.largeTextEnabled, true);
  assert.equal(context.data.homePageClass, "home-page large-text");
  assert.equal(homeViewPatch({ ...context.data, largeTextEnabled: false }).homePageClass, "home-page");
});

test("home presents internal project titles as natural story copy", () => {
  assert.equal(memoirStoryTitle("爷爷主题"), "爷爷的故事");
  assert.equal(memoirStoryTitle("外婆回忆录"), "外婆回忆录");
  assert.equal(memoirHomepageTitle("外婆回忆录"), "外婆回忆录");
  assert.equal(memoirHomepageTitle("我的回忆录"), "我的回忆录");
  assert.equal(memoirHomepageTitle("爷爷主题"), "爷爷主题");
  assert.equal(memoirHomepageTitle(""), "还没有回忆录");
  assert.equal(memoirHomePrompt("爷爷主题"), "不用讲完整，可以先说一件和爷爷有关的小事。");
});

test("home creates concise resume hints from the latest transcript", () => {
  const { homeResumeHint } = loadHomePage().mod;

  assert.equal(homeResumeHint({
    summary_title: "爷爷退休后还在忙公司的事。",
    summary_text: "",
    display_text: "",
    transcript_edited: "",
    transcript_raw: "",
  }), "爷爷退休后还在忙公司的事");
  assert.equal(homeResumeHint({
    summary_title: "",
    summary_text: "  ",
    display_text: "那年在县城读书，住在外婆家。",
    transcript_edited: "",
    transcript_raw: "",
  }), "那年在县城读书，住在外婆家");
  assert.equal(homeResumeHint({
    summary_title: "这是一段很长很长的讲述标题，用来确认首页只展示一句短短的续讲提示",
    summary_text: "",
    display_text: "",
    transcript_edited: "",
    transcript_raw: "",
  }), "这是一段很长很长的讲述标题，用来确认首页只展示一句短短的...");
});

test("home view patch keeps first-screen template state simple", () => {
  const loaded = loadHomePage();
  const { homeViewPatch } = loaded.mod;
  const baseData = {
    ...loaded.page.data,
    needsLogin: false,
    userId: "user-1",
    displayName: "余主任",
    projectId: "project-1",
    projectTitle: "爷爷主题",
    projectDisplayTitle: "爷爷的故事",
    lastStoryHint: "爷爷退休后还在忙公司的事",
    hasLastStoryHint: true,
    launchVariant: "new_project",
    hasProject: true,
    hasStories: true,
    selectedMode: "free_narration",
    voiceAuthorizationAgreed: true,
    voiceAuthorizationRequested: false,
  };

  assert.deepEqual(
    {
      greetingText: homeViewPatch(baseData).greetingText,
      headerSubtitleText: homeViewPatch(baseData).headerSubtitleText,
      primaryVoiceButtonTitle: homeViewPatch(baseData).primaryVoiceButtonTitle,
      showNarrationChoiceCard: homeViewPatch(baseData).showNarrationChoiceCard,
      voiceStartButtonClass: homeViewPatch({ ...baseData, creatingVoice: true }).voiceStartButtonClass,
      voiceStartButtonHoverClass: homeViewPatch({ ...baseData, creatingVoice: true }).voiceStartButtonHoverClass,
      showVoiceAuthorizationPanel: homeViewPatch(baseData).showVoiceAuthorizationPanel,
    },
    {
      greetingText: "你好，余主任",
      headerSubtitleText: "",
      primaryVoiceButtonTitle: "讲个新故事",
      showNarrationChoiceCard: true,
      voiceStartButtonClass: "button-blocked",
      voiceStartButtonHoverClass: "none",
      showVoiceAuthorizationPanel: false,
    },
  );
});

test("home start action follows account, project, story and completion states", () => {
  const { homeViewPatch } = loadHomePage().mod;
  const baseData = {
    ...loadHomePage().page.data,
    hasProject: true,
    needsLogin: false,
    voiceAuthorizationAgreed: true,
    hasStories: false,
  };

  const guided = homeViewPatch({
    ...baseData,
    selectedMode: "guided_interview",
  });
  assert.equal(guided.primaryVoiceButtonTitle, "讲第一个故事");

  const free = homeViewPatch({ ...baseData, selectedMode: "free_narration" });
  assert.equal(free.primaryVoiceButtonTitle, "讲第一个故事");
  assert.equal(homeViewPatch({ ...baseData, hasStories: true }).primaryVoiceButtonTitle, "讲个新故事");
  assert.equal(homeViewPatch({ ...baseData, hasProject: false }).primaryVoiceButtonTitle, "种下我的故事");
  assert.equal(homeViewPatch({ ...baseData, needsLogin: true }).primaryVoiceButtonTitle, "种下我的故事");
  assert.equal(homeViewPatch({ ...baseData, selectedMode: "book_completion" }).primaryVoiceButtonTitle, "选择待补问题");
  assert.equal(homeViewPatch({ ...baseData, hasStories: true, hasLastStoryHint: true }).showNarrationChoiceCard, true);
  assert.equal(homeViewPatch({ ...baseData, hasStories: true, hasLastStoryHint: false }).showNarrationChoiceCard, false);
  assert.equal(homeViewPatch({ ...baseData, hasStories: true, hasLastStoryHint: true, selectedMode: "book_completion" }).showNarrationChoiceCard, false);
});

test("home reveals voice consent only after a start requests it", () => {
  const loaded = loadHomePage();
  const { homeViewPatch } = loaded.mod;
  const baseData = {
    ...loaded.page.data,
    loading: false,
    needsLogin: false,
    hasProject: true,
    voiceAuthorizationAgreed: false,
    voiceAuthorizationRequested: false,
  };

  assert.equal(homeViewPatch(baseData).showVoiceAuthorizationPanel, false);
  assert.equal(homeViewPatch({ ...baseData, voiceAuthorizationRequested: true }).showVoiceAuthorizationPanel, true);
  assert.equal(homeViewPatch({ ...baseData, loading: true }).showVoiceAuthorizationPanel, false);
  assert.equal(homeViewPatch({ ...baseData, hasProject: false, voiceAuthorizationRequested: true }).showVoiceAuthorizationPanel, true);
  assert.equal(homeViewPatch({ ...baseData, needsLogin: true, voiceAuthorizationRequested: true }).showVoiceAuthorizationPanel, false);
});

test("home starts all three regular modes from the saved project default", async () => {
  const created = [];
  global.wx = { pageScrollTo() {} };
  const { page } = loadHomePage({ voiceApiExports: {
    createMemoirVoiceSession: async input => { created.push(input); return { session: { id: `s${created.length}` } }; },
  } });
  const context = pageContext(page, { projectId: "p1", userId: "u1", hasProject: true, voiceAuthorizationAgreed: true });
  context.measureNarration = () => {};
  for (const mode of ["guided_interview", "free_narration", "human_interview"]) {
    context.data.selectedMode = mode;
    await context.startPrimaryVoiceChat();
    assert.equal(created.at(-1).guidance_mode, mode === "human_interview" ? "free_narration" : mode);
    assert.equal(created.at(-1).interview_type, mode === "human_interview" ? "human_interview" : "ai_interview");
    assert.equal(created.at(-1).session_goal, "open_narration");
    context.data.voiceEntry = null;
  }
});

test("home routes the book-completion default to the scoped issue list", async () => {
  const navigations = [];
  let creates = 0;
  global.wx = {
    navigateTo(options) {
      navigations.push(options.url);
    },
  };
  const { page } = loadHomePage({ voiceApiExports: {
    createMemoirVoiceSession: async () => { creates += 1; },
  } });
  const context = pageContext(page, {
    projectId: "project/1",
    projectTitle: "爷爷的故事",
    projectDisplayTitle: "爷爷的故事",
    hasProject: true,
    selectedMode: "book_completion",
    voiceAuthorizationAgreed: false,
  });

  await context.startPrimaryVoiceChat();

  assert.equal(creates, 0);
  assert.equal(context.data.voiceAuthorizationRequested, false);
  assert.equal(navigations.length, 1);
  const url = new URL(navigations[0], "https://miniprogram.test");
  assert.equal(url.pathname, "/pages/book-completion/index");
  assert.equal(url.searchParams.get("projectId"), "project/1");
});

test("home userDisplayName hides wechat placeholder display name", () => {
  assert.equal(userDisplayName("微信用户", "memoir-author@example.com"), "memoir-author");
});

test("home userDisplayName hides provisional wechat local email", () => {
  assert.equal(userDisplayName("微信用户", "wechat_1234567890abcdef12345678@wechat.local"), "作者");
});

test("home userDisplayName keeps real display names", () => {
  assert.equal(userDisplayName("  余主任  ", "memoir-author@example.com"), "余主任");
});

test("home treats wechat-only accounts as logged in users without projects", async () => {
  const { page } = loadHomePage({
    authExports: {
      getMemoirIdentity: async () => ({
        id: "user-1",
        email: "wechat_1234567890abcdef12345678@wechat.local",
        password_login_enabled: false,
        display_name: "微信昵称",
        created_at: "2026-06-15T00:00:00",
        active_project_id: "",
        active_project: null,
        projects: [],
      }),
    },
  });
  const context = {
    ...page,
    data: { ...page.data },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  await page.loadHome.call(context);

  assert.equal(context.data.needsLogin, false);
  assert.equal(context.data.displayName, "微信昵称");
  assert.equal(context.data.projectTitle, "还没有回忆录");
  assert.equal(context.data.hasProject, false);
  assert.equal(context.data.loading, false);
});

test("home refreshes guidance mode from current project on load", async () => {
  const transcriptCalls = [];
  const { page } = loadHomePage({
    authExports: {
      getMemoirIdentity: async () => ({
        id: "user-1",
        email: "memoir-author@example.com",
        password_login_enabled: true,
        display_name: "作者",
        created_at: "2026-06-15T00:00:00",
        active_project_id: "project-1",
        active_project: {
          id: "project-1",
          title: "外婆回忆录",
          metadata_json: {
            memoir_profile: {
              default_guidance_mode: "guided_interview",
            },
          },
        },
        projects: [],
      }),
    },
    voiceApiExports: {
      listMemoirVoiceTranscripts: async (options) => {
        transcriptCalls.push(options);
        return {
          items: [{
            summary_title: "爷爷退休后还在忙公司的事。",
            summary_text: "",
            display_text: "",
            transcript_edited: "",
            transcript_raw: "",
          }],
          total: 1,
          limit: 1,
          offset: 0,
        };
      },
    },
  });
  const context = {
    ...page,
    data: { ...page.data },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  await page.loadHome.call(context);

  assert.equal(context.data.selectedMode, "guided_interview");
  assert.equal(context.data.projectTitle, "外婆回忆录");
  assert.equal(context.data.projectDisplayTitle, "外婆回忆录");
  assert.equal(context.data.hasProject, true);
  assert.deepEqual(transcriptCalls, [{ projectId: "project-1", limit: 1 }]);
  assert.equal(context.data.lastStoryHint, "爷爷退休后还在忙公司的事");
  assert.equal(context.data.hasLastStoryHint, true);
});

test("home paints cached memoir data before the aggregate refresh completes", async () => {
  const refresh = deferred();
  const cachedSnapshot = {
    server_revision: 2,
    identity: {
      id: "user-1",
      email: "memoir-author@example.com",
      password_login_enabled: true,
      display_name: "作者",
      created_at: "2026-06-15T00:00:00",
      active_project_id: "project-1",
      active_project: {
        id: "project-1",
        title: "缓存里的回忆录",
        metadata_json: {},
      },
      projects: [],
    },
    latest_transcript: null,
  };
  const refreshedSnapshot = {
    ...cachedSnapshot,
    chapters: [], progress: null, growth_events: [], book_readable: false, book_char_count: 0, updated_at: "", unassigned_image_count: 0, unassigned_video_count: 0,
    server_revision: 3,
    identity: {
      ...cachedSnapshot.identity,
      active_project: {
        ...cachedSnapshot.identity.active_project,
        title: "服务器上的回忆录",
      },
    },
  };
  const writes = [];
  const { page } = loadHomePage({
    voiceApiExports: {
      getMemoirStorySnapshot: () => refresh.promise,
    },
    cacheExports: {
      readCachedMemoirHome: () => ({
        status: "fresh",
        payload: cachedSnapshot,
        localInvalidationRevision: 0,
      }),
      writeCachedMemoirHome: (snapshot) => writes.push(snapshot.server_revision),
    },
  });
  const context = {
    ...page,
    data: { ...page.data },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  const loading = page.loadHome.call(context);
  assert.equal(context.data.projectTitle, "缓存里的回忆录");
  assert.equal(context.data.loading, false);

  refresh.resolve(refreshedSnapshot);
  await loading;

  assert.equal(context.data.projectTitle, "服务器上的回忆录");
  assert.deepEqual(writes, [3]);
});

test("home shows explicit project creation state for logged-in users without projects", async () => {
  const { page } = loadHomePage({
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
  });
  const context = {
    ...page,
    data: { ...page.data },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  await page.loadHome.call(context);

  assert.equal(context.data.needsLogin, false);
  assert.equal(context.data.projectId, "");
  assert.equal(context.data.projectTitle, "还没有回忆录");
  assert.equal(context.data.hasProject, false);
});

test("home keeps first-visit context while presenting the seed state", async () => {
  const { page } = loadHomePage({
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
    launchStateExports: {
      resolveHomeLaunch: () => "first_visit",
    },
  });
  const context = {
    ...page,
    data: { ...page.data },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  await page.loadHome.call(context);

  assert.equal(context.data.launchVariant, "first_visit");
  assert.equal(context.data.primaryVoiceButtonTitle, "种下我的故事");
  assert.equal(context.data.headerSubtitleText, "每一个故事，都从一颗种子开始。");
});

test("home dismisses one-time new-project launch markers", () => {
  const dismissals = [];
  const { page } = loadHomePage({
    launchStateExports: {
      dismissHomeLaunch: (...args) => dismissals.push(args),
    },
  });
  const context = {
    ...page,
    data: {
      ...page.data,
      userId: "user-1",
      projectId: "project-1",
      launchVariant: "new_project",
    },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  page.dismissLaunchExperience.call(context);

  assert.deepEqual(dismissals, [["user-1", "new_project", "project-1"]]);
  assert.equal(context.data.launchVariant, "none");
});

test("home creates voice sessions with the project-bound guidance mode", async () => {
  const createdSessions = [];
  const navigations = [];
  const syncedConsents = [];
  global.wx = {
    navigateTo(options) {
      navigations.push(options.url);
    },
  };
  const { page } = loadHomePage({
    voiceApiExports: {
      createMemoirVoiceSession: async (input, options) => {
        createdSessions.push({ input, options });
        return {
          session: {
            id: "voice/1",
          },
        };
      },
    },
    voiceAuthorizationExports: {
      syncVoiceAuthorizationConsent: async (options) => {
        syncedConsents.push(options || {});
        return { id: "consent-1" };
      },
    },
  });
  const context = {
    ...page,
    data: {
      ...page.data,
      selectedMode: "guided_interview",
      projectId: "project/1",
      projectTitle: "爷爷的故事",
      hasProject: true,
      voiceAuthorizationAgreed: true,
    },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  await page.startPrimaryVoiceChat.call(context);

  assert.equal(createdSessions.length, 1);
  assert.equal(syncedConsents.length, 1);
  assert.equal(createdSessions[0].input.guidance_mode, "guided_interview");
  assert.equal(createdSessions[0].input.session_goal, "open_narration");
  assert.equal(createdSessions[0].input.current_guidance_module, undefined);
  assert.equal(createdSessions[0].input.metadata_json.entry, "miniprogram_home");
  assert.deepEqual(createdSessions[0].options, { projectId: "project/1" });
  assert.deepEqual(navigations, []);
  assert.deepEqual(context.data.voiceEntry, { voiceSessionId: "voice/1", projectId: "project/1", mode: "guided_interview", interviewType: "ai_interview", sessionGoal: "open_narration", projectTitle: "爷爷的故事" });
  assert.equal(context.data.creatingVoice, false);
});

test("home creates free narration voice sessions without a guidance module", async () => {
  const createdSessions = [];
  const navigations = [];
  global.wx = {
    navigateTo(options) {
      navigations.push(options.url);
    },
  };
  const { page } = loadHomePage({
    voiceApiExports: {
      createMemoirVoiceSession: async (input, options) => {
        createdSessions.push({ input, options });
        return {
          session: {
            id: "voice/free",
          },
        };
      },
    },
  });
  const context = {
    ...page,
    data: {
      ...page.data,
      selectedMode: "free_narration",
      projectId: "project/1",
      projectTitle: "自由回忆",
      hasProject: true,
      voiceAuthorizationAgreed: true,
    },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  await page.startPrimaryVoiceChat.call(context);

  assert.equal(createdSessions.length, 1);
  assert.equal(createdSessions[0].input.guidance_mode, "free_narration");
  assert.equal(createdSessions[0].input.session_goal, "open_narration");
  assert.equal(createdSessions[0].input.current_guidance_module, undefined);
  assert.deepEqual(navigations, []);
  assert.equal(context.data.voiceEntry.voiceSessionId, "voice/free");
  assert.equal(context.data.voiceEntry.mode, "free_narration");
});

test("home keeps new-story and resume actions distinct across narration modes", async () => {
  const createdSessions = [];
  const navigations = [];
  global.wx = {
    navigateTo(options) {
      navigations.push(options.url);
    },
  };
  const { page } = loadHomePage({
    voiceApiExports: {
      createMemoirVoiceSession: async (input, options) => {
        createdSessions.push({ input, options });
        return {
          session: {
            id: `voice/${createdSessions.length}`,
          },
        };
      },
    },
  });
  const context = {
    ...page,
    data: {
      ...page.data,
      projectId: "project/1",
      projectTitle: "爷爷的故事",
      projectDisplayTitle: "爷爷的故事",
      hasProject: true,
      voiceAuthorizationAgreed: true,
      lastStoryHint: "爷爷退休后还在忙公司的事",
      hasLastStoryHint: true,
    },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  await page.startPrimaryVoiceChat.call(context);
  context.data.voiceEntry = null; // The prior component has completed and unmounted.
  await context.resumeLastStory();
  context.data.voiceEntry = null;
  context.data.selectedMode = "human_interview";
  await context.startPrimaryVoiceChat();

  assert.equal(createdSessions[0].input.guidance_mode, "free_narration");
  assert.equal(createdSessions[0].input.metadata_json.entry, "miniprogram_home");
  assert.equal(createdSessions[0].input.metadata_json.resume_hint, undefined);
  assert.equal(createdSessions[1].input.metadata_json.entry, "miniprogram_home_resume");
  assert.equal(createdSessions[1].input.metadata_json.resume_hint, "爷爷退休后还在忙公司的事");
  assert.equal(createdSessions[2].input.guidance_mode, "free_narration");
  assert.equal(createdSessions[2].input.interview_type, "human_interview");
  assert.equal(createdSessions[2].input.metadata_json.entry, "miniprogram_home");
  assert.equal(createdSessions[2].input.metadata_json.resume_hint, undefined);
  assert.equal(createdSessions[2].input.metadata_json.interview_type, "human_interview");
  assert.equal(navigations.length, 0);
  assert.equal(context.data.voiceEntry.interviewType, "human_interview");
});

test("home does not create voice sessions when consent audit sync fails", async () => {
  let createCalls = 0;
  const { page } = loadHomePage({
    voiceApiExports: {
      createMemoirVoiceSession: async () => {
        createCalls += 1;
        return { session: { id: "voice/blocked" } };
      },
    },
    voiceAuthorizationExports: {
      syncVoiceAuthorizationConsent: async () => {
        throw new Error("授权同步失败");
      },
    },
  });
  const context = {
    ...page,
    data: {
      ...page.data,
      projectId: "project/1",
      hasProject: true,
      voiceAuthorizationAgreed: true,
    },
    setData(patch, callback) {
      this.data = { ...this.data, ...patch };
      callback?.();
    },
  };

  await page.startPrimaryVoiceChat.call(context);

  assert.equal(createCalls, 0);
  assert.equal(context.data.error, "授权同步失败");
  assert.equal(context.data.creatingVoice, false);
});

test("home creates the first memoir and starts narrating without the setup form", async () => {
  const navigations = [];
  const createdProjects = [];
  const sessionProjects = [];
  let project = null;
  global.wx = {
    navigateTo(options) { navigations.push(options.url); },
    pageScrollTo() {},
  };
  const { page } = loadHomePage({
    authExports: {
      getMemoirIdentity: async () => ({
        id: "u1", display_name: "", email: "",
        active_project_id: project?.id || "", active_project: project, projects: project ? [project] : [],
      }),
    },
    projectExports: {
      createMemoirProject: async (input) => {
        createdProjects.push(input);
        project = { id: "p-first", title: input.title, metadata_json: {} };
        return project;
      },
    },
    voiceApiExports: {
      createMemoirVoiceSession: async (_input, { projectId }) => {
        sessionProjects.push(projectId);
        return { session: { id: "voice-first" } };
      },
    },
  });
  const context = pageContext(page, {
    userId: "u1", needsLogin: false, projectId: "", hasProject: false, voiceAuthorizationAgreed: true,
  });
  context.measureNarration = () => {};

  await context.startPrimaryVoiceChat();

  assert.deepEqual(createdProjects, [{ title: "我的回忆录" }]);
  assert.deepEqual(sessionProjects, ["p-first"]);
  assert.deepEqual(navigations, []);
  assert.equal(context.data.voiceEntry.voiceSessionId, "voice-first");
  delete global.wx;
});

test("agreeing to voice consent carries on with the story the user tapped", async () => {
  let storedConsent = false;
  let creates = 0;
  global.wx = { pageScrollTo() {} };
  const { page } = loadHomePage({
    voiceAuthorizationExports: {
      readVoiceAuthorizationConsent: () => storedConsent,
      syncVoiceAuthorizationConsent: async ({ consent } = {}) => {
        if (consent) storedConsent = consent.agreed;
        return { id: "consent-1" };
      },
    },
    voiceApiExports: {
      createMemoirVoiceSession: async () => {
        creates += 1;
        return { session: { id: "s1" } };
      },
    },
  });
  const context = pageContext(page, {
    userId: "u1", needsLogin: false, projectId: "p1", hasProject: true, voiceAuthorizationAgreed: false,
  });
  context.measureNarration = () => {};

  await context.startPrimaryVoiceChat();
  assert.equal(creates, 0);
  assert.equal(context.data.showVoiceAuthorizationPanel, true);

  await context.changeVoiceAuthorization({ detail: { value: ["voice_authorization"] } });
  assert.equal(creates, 1);
  assert.equal(context.data.voiceEntry.voiceSessionId, "s1");
  delete global.wx;
});

test("logging in from the story button carries on into narration", async () => {
  const navigations = [];
  let creates = 0;
  global.wx = {
    navigateTo(options) { navigations.push(options.url); },
    pageScrollTo() {},
  };
  const project = { id: "p1", title: "我的回忆录", metadata_json: {} };
  const { page } = loadHomePage({
    authExports: {
      getMemoirIdentity: async () => ({
        id: "u1", display_name: "", email: "", active_project_id: "p1", active_project: project, projects: [project],
      }),
    },
    voiceApiExports: {
      createMemoirVoiceSession: async () => {
        creates += 1;
        return { session: { id: "s1" } };
      },
    },
  });
  const loggedOut = pageContext(page, { needsLogin: true, voiceAuthorizationAgreed: true });
  await loggedOut.startPrimaryVoiceChat();
  assert.deepEqual(navigations, ["/pages/login/index"]);

  const relaunched = pageContext(page, { needsLogin: true, voiceAuthorizationAgreed: true });
  relaunched.measureNarration = () => {};
  await relaunched.onShow();
  assert.equal(creates, 1);

  await relaunched.onHide();
  await relaunched.onShow();
  assert.equal(creates, 1);
  delete global.wx;
});

test("home sends login-required voice starts to the dedicated login page", async () => {
  const navigations = [];
  global.wx = {
    navigateTo(options) {
      navigations.push(options.url);
    },
  };
  const { page } = loadHomePage();
  const context = {
    ...page,
    data: {
      ...page.data,
      needsLogin: true,
      voiceAuthorizationAgreed: true,
    },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  await page.startPrimaryVoiceChat.call(context);

  assert.deepEqual(navigations, ["/pages/login/index"]);
});

test("home opens the project switcher from the current memoir", () => {
  const navigations = [];
  global.wx = {
    navigateTo(options) {
      navigations.push(options.url);
    },
  };
  const { page } = loadHomePage();
  const context = {
    ...page,
    data: {
      ...page.data,
      needsLogin: false,
      projectId: "project-1",
      hasProject: true,
    },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  page.openProjects.call(context);

  assert.deepEqual(navigations, ["/pages/projects/index"]);
});

test("home blocks voice session creation before voice authorization consent", async () => {
  let createCalls = 0;
  const scrolls = [];
  global.wx = {
    pageScrollTo(options) { scrolls.push(options); },
  };
  const { page } = loadHomePage({
    voiceApiExports: {
      createMemoirVoiceSession: async () => {
        createCalls += 1;
        return { session: { id: "voice/blocked" } };
      },
    },
  });
  const context = {
    ...page,
    data: {
      ...page.data,
      needsLogin: false,
      projectId: "project/1",
      hasProject: true,
      voiceAuthorizationAgreed: false,
    },
    setData(patch, callback) {
      this.data = { ...this.data, ...patch };
      callback?.();
    },
  };

  await page.startPrimaryVoiceChat.call(context);

  assert.equal(createCalls, 0);
  assert.equal(context.data.error, "");
  assert.equal(context.data.voiceAuthorizationRequested, true);
  assert.equal(context.data.showVoiceAuthorizationPanel, true);
  assert.deepEqual(scrolls, [{ selector: "#home-voice-authorization", duration: 250 }]);
});

test("home refreshes voice authorization consent from storage", () => {
  let agreed = false;
  const { page } = loadHomePage({
    voiceAuthorizationExports: {
      readVoiceAuthorizationConsent: () => agreed,
    },
  });
  const context = {
    ...page,
    data: { ...page.data, voiceAuthorizationAgreed: true },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  page.refreshVoiceAuthorizationConsent.call(context);
  assert.equal(context.data.voiceAuthorizationAgreed, false);

  agreed = true;
  page.refreshVoiceAuthorizationConsent.call(context);
  assert.equal(context.data.voiceAuthorizationAgreed, true);
});

test("home waits for voice authorization to be saved before starting a session", async () => {
  const consentWrite = deferred();
  let storedConsent = false;
  let creates = 0;
  const { page } = loadHomePage({
    voiceAuthorizationExports: {
      readVoiceAuthorizationConsent: () => storedConsent,
      syncVoiceAuthorizationConsent: async ({ consent } = {}) => {
        if (consent) {
          await consentWrite.promise;
          storedConsent = consent.agreed;
        } else if (!storedConsent) {
          throw new Error("授权还没有写入服务端");
        }
        return { id: "consent-1" };
      },
    },
    voiceApiExports: {
      createMemoirVoiceSession: async () => {
        creates += 1;
        return { session: { id: "s1" } };
      },
    },
  });
  global.wx = { pageScrollTo() {} };
  const context = pageContext(page, {
    userId: "u1", projectId: "p1", hasProject: true,
    voiceAuthorizationAgreed: false, voiceAuthorizationRequested: true,
  });
  context.measureNarration = () => {};

  const saving = context.changeVoiceAuthorization({
    detail: { value: ["voice_authorization"] },
  });
  assert.equal(context.data.voiceAuthorizationSaving, true);
  assert.equal(context.data.voiceAuthorizationAgreed, true);
  await context.startPrimaryVoiceChat();
  assert.equal(creates, 0);
  consentWrite.resolve();
  await saving;
  assert.equal(context.data.voiceAuthorizationSaving, false);
  assert.equal(storedConsent, true);
  await context.startPrimaryVoiceChat();
  assert.equal(creates, 1);
  delete global.wx;
});

test("home keeps consent available for retry when saving fails", async () => {
  const { page } = loadHomePage({
    voiceAuthorizationExports: {
      readVoiceAuthorizationConsent: () => false,
      syncVoiceAuthorizationConsent: async () => { throw new Error("授权保存失败"); },
    },
  });
  const context = pageContext(page, {
    userId: "u1", projectId: "p1", hasProject: true,
    voiceAuthorizationAgreed: false, voiceAuthorizationRequested: true,
  });
  context.measureNarration = () => {};

  await context.changeVoiceAuthorization({ detail: { value: ["voice_authorization"] } });

  assert.equal(context.data.voiceAuthorizationSaving, false);
  assert.equal(context.data.voiceAuthorizationAgreed, false);
  assert.equal(context.data.showVoiceAuthorizationPanel, true);
  assert.equal(context.data.error, "授权保存失败");
});

test("home refreshes the saved project default for the current account and project", async () => {
  let identity = { id: "u1", display_name: "作者", email: "author@test.invalid", active_project_id: "p1",
    active_project: { id: "p1", title: "回忆录", metadata_json: { memoir_profile: {
      default_guidance_mode: "free_narration", default_interview_type: "ai_interview", default_session_goal: "open_narration",
    } } } };
  const { page } = loadHomePage({ authExports: { getMemoirIdentity: async () => identity },
    cacheExports: { readCachedMemoirHome: () => ({ payload: { identity, latest_transcript: null, server_revision: 1 } }) },
  });
  const context = pageContext(page);
  context.measureNarration = () => {};
  await context.onShow();
  assert.equal(context.data.selectedMode, "free_narration");
  identity = { ...identity, active_project: { ...identity.active_project, metadata_json: { memoir_profile: {
    default_guidance_mode: "guided_interview", default_interview_type: "ai_interview", default_session_goal: "open_narration",
  } } } };
  await context.loadHome(true);
  assert.equal(context.data.selectedMode, "guided_interview");
  identity = { ...identity, active_project: { ...identity.active_project, metadata_json: { memoir_profile: {
    default_guidance_mode: "free_narration", default_interview_type: "human_interview", default_session_goal: "open_narration",
  } } } };
  await context.loadHome(true);
  assert.equal(context.data.selectedMode, "human_interview");
  identity = { ...identity, active_project: { ...identity.active_project, metadata_json: { memoir_profile: {
    default_guidance_mode: "guided_interview", default_interview_type: "ai_interview", default_session_goal: "book_completion",
  } } } };
  await context.loadHome(true);
  assert.equal(context.data.selectedMode, "book_completion");
  identity = { ...identity, id: "u2" };
  await context.loadHome(true);
  assert.equal(context.data.selectedMode, "book_completion");
  identity = { ...identity, active_project_id: "p2", active_project: { ...identity.active_project, id: "p2",
    metadata_json: { memoir_profile: {
      default_guidance_mode: "guided_interview", default_interview_type: "ai_interview", default_session_goal: "open_narration",
    } } } };
  context.data.voiceEntry = { voiceSessionId: "old-session" };
  context.data.selectedMode = "human_interview";
  await context.loadHome(true);
  assert.equal(context.data.selectedMode, "guided_interview");
  assert.equal(context.data.voiceEntry, null);
  context.onUnload();
});

test("visitors browse the sample memoir and open only its readable chapters", async () => {
  const { ApiError } = require("../services/request.ts");
  const chapter = (id, title, readable) => ({
    id, title, readable, include_in_book: true, material_count: 1, image_count: 0, video_count: 0,
    char_count: readable ? 120 : 80, candidate_job_id: null, state: readable ? "settled" : "draft",
    state_text: "", issues: { review_count: 0, supplement_count: 0 }, sections: [],
  });
  const { page } = loadHomePage({ voiceApiExports: {
    getMemoirStorySnapshot: async () => { throw new ApiError(401, "请先登录", {}); },
    getMemoirDemoStory: async () => ({
      title: "余仲尤往事",
      chapters: [chapter("family", "家里的人", true), chapter("closed", "下乡那几年", false)],
      book_chapter_count: 2,
      book_char_count: 200,
      videos: [{ share_code: "code-1", title: "他端来半碗胡豆", poster_url: "/poster" }],
    }),
  } });
  const opened = [];
  const toasts = [];
  global.wx = {
    navigateTo: ({ url }) => opened.push(url),
    showToast: ({ title }) => toasts.push(title),
  };
  const context = pageContext(page, {});

  await page.loadHome.call(context);
  await flushAsyncWork();

  assert.equal(context.data.needsLogin, true);
  assert.equal(context.data.storyLoaded, true);
  assert.deepEqual(context.data.story.chapters.map((item) => item.title), ["家里的人", "下乡那几年"]);

  context.openChapter({ detail: { chapterId: "family" } });
  context.openChapter({ detail: { chapterId: "closed" } });
  context.openBook();
  assert.deepEqual(opened, [
    "/pages/book-reader/index?demo=1",
    "/pages/book-reader/index?demo=1",
  ]);
  assert.equal(toasts.length, 1);
});

test("a failed demo refresh clears old public links and remains retryable without login", async () => {
  const { ApiError } = require("../services/request.ts");
  const { page } = loadHomePage({ voiceApiExports: {
    getMemoirStorySnapshot: async () => { throw new ApiError(401, "请先登录", {}); },
    getMemoirDemoStory: async () => { throw new Error("offline"); },
  } });
  const context = pageContext(page, {
    needsLogin: true,
    demoReadableChapterIds: ["old-chapter"],
  });
  await context.loadHome();
  assert.equal(context.data.needsLogin, true);
  assert.equal(context.data.loading, false);
  assert.ok(context.data.storyError);
  assert.deepEqual(context.data.demoReadableChapterIds, []);
});
