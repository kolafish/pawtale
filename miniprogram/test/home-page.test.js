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
        book_readable: false, book_char_count: 0, updated_at: "", unassigned_image_count: 0, unassigned_video_count: 0,
        identity,
        latest_transcript: latest.items[0] || null,
      };
    },
    ...voiceApiExports,
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

test("mini program opens on the browseable home tab", () => {
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
    setData(patch) {
      this.data = { ...this.data, ...patch };
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
  const { homeViewPatch } = loadHomePage().mod;
  const baseData = {
    loading: false,
    creatingVoice: false,
    needsLogin: false,
    error: "",
    userId: "user-1",
    displayName: "余主任",
    projectId: "project-1",
    projectTitle: "爷爷主题",
    projectDisplayTitle: "爷爷的故事",
    projectPromptText: "不用讲完整，可以先说一件和爷爷有关的小事。",
    lastStoryHint: "爷爷退休后还在忙公司的事",
    lastStoryText: "上次您说到：爷爷退休后还在忙公司的事。",
    hasLastStoryHint: true,
    launchVariant: "new_project",
    showLaunchPanel: true,
    showReturnPrompt: false,
    launchEyebrow: "新的故事本",
    launchTitle: "爷爷的故事，慢慢讲",
    launchBody: "",
    launchPrimaryText: "今天先聊一段",
    launchSecondaryText: "先看看首页",
    hasProject: true,
    selectedMode: "free_narration",
    voiceAuthorizationAgreed: true,
  };

  assert.deepEqual(
    {
      greetingText: homeViewPatch(baseData).greetingText,
      launchModeTagText: homeViewPatch(baseData).launchModeTagText,
      projectHeadingText: homeViewPatch(baseData).projectHeadingText,
      projectMetaText: homeViewPatch(baseData).projectMetaText,
      primaryVoiceButtonTitle: homeViewPatch(baseData).primaryVoiceButtonTitle,
      voiceStartButtonClass: homeViewPatch({ ...baseData, creatingVoice: true }).voiceStartButtonClass,
      voiceStartButtonHoverClass: homeViewPatch({ ...baseData, creatingVoice: true }).voiceStartButtonHoverClass,
      showResumeActions: homeViewPatch(baseData).showResumeActions,
      moreActionsToggleText: homeViewPatch(baseData).moreActionsToggleText,
      alternateModeButtonTitle: homeViewPatch(baseData).alternateModeButtonTitle,
      showVoiceAuthorizationPanel: homeViewPatch(baseData).showVoiceAuthorizationPanel,
    },
    {
      greetingText: "你好，余主任",
      launchModeTagText: "新故事本",
      projectHeadingText: "爷爷的故事",
      projectMetaText: "上次您说到：爷爷退休后还在忙公司的事。",
      primaryVoiceButtonTitle: "随便讲一段",
      voiceStartButtonClass: "voice-start-button button-blocked",
      voiceStartButtonHoverClass: "none",
      showResumeActions: true,
      moreActionsToggleText: "其他讲述方式",
      alternateModeButtonTitle: "让小余陪着问",
      showVoiceAuthorizationPanel: false,
    },
  );
});

test("home primary CTA and alternate action follow the current project's guidance mode", () => {
  const { homeViewPatch } = loadHomePage().mod;
  const baseData = {
    ...loadHomePage().page.data,
    hasProject: true,
    needsLogin: false,
    voiceAuthorizationAgreed: true,
  };

  const guided = homeViewPatch({
    ...baseData,
    selectedMode: "guided_interview",
  });
  assert.equal(guided.currentGuidanceText, "当前讲述方式：小余陪着问");
  assert.equal(guided.primaryVoiceButtonTitle, "让小余陪着问");
  assert.equal(guided.voiceStartNoteText, "小余会从一件具体的事开始问起。");
  assert.equal(guided.alternateModeButtonTitle, "我想自己讲");

  const free = homeViewPatch({ ...baseData, selectedMode: "free_narration" });
  assert.equal(free.currentGuidanceText, "当前讲述方式：自由讲述");
  assert.equal(free.primaryVoiceButtonTitle, "随便讲一段");
  assert.equal(free.alternateModeButtonTitle, "让小余陪着问");

  const expanded = homeViewPatch({
    ...baseData,
    showMoreActions: true,
  });
  assert.equal(expanded.moreActionsToggleText, "收起其他讲述方式");
  assert.equal(expanded.moreActionsToggleAriaLabel, "收起其他讲述方式");
});

test("home only shows project actions and voice consent after a usable project has loaded", () => {
  const loaded = loadHomePage();
  const { homeViewPatch } = loaded.mod;
  const baseData = {
    ...loaded.page.data,
    loading: false,
    needsLogin: false,
    hasProject: true,
    voiceAuthorizationAgreed: false,
  };

  assert.equal(homeViewPatch(baseData).showResumeActions, true);
  assert.equal(homeViewPatch(baseData).showVoiceAuthorizationPanel, true);
  assert.equal(homeViewPatch({ ...baseData, loading: true }).showResumeActions, false);
  assert.equal(homeViewPatch({ ...baseData, loading: true }).showVoiceAuthorizationPanel, false);
  assert.equal(homeViewPatch({ ...baseData, hasProject: false }).showVoiceAuthorizationPanel, false);
  assert.equal(homeViewPatch({ ...baseData, needsLogin: true }).showVoiceAuthorizationPanel, false);
});

test("home primary and alternate CTAs create sessions with their displayed guidance modes", async () => {
  const createdModes = [];
  global.wx = {
    navigateTo() {},
  };
  const { page } = loadHomePage({
    voiceApiExports: {
      createMemoirVoiceSession: async (input) => {
        createdModes.push(input.guidance_mode);
        return { session: { id: `voice-${createdModes.length}` } };
      },
    },
  });
  const context = {
    ...page,
    data: {
      ...page.data,
      selectedMode: "guided_interview",
      projectId: "project-1",
      hasProject: true,
      voiceAuthorizationAgreed: true,
    },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  for (const mode of ["guided_interview", "free_narration"]) {
    context.data.selectedMode = mode;
    await page.startPrimaryVoiceChat.call(context);
    context.data.voiceEntry = null;
    await page.startAlternateGuidance.call(context);
    context.data.voiceEntry = null;
    assert.equal(context.data.selectedMode, mode, "starting an alternate session preserves the project default");
  }

  assert.deepEqual(createdModes, ["guided_interview", "free_narration", "free_narration", "guided_interview"]);
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
  assert.equal(context.data.currentGuidanceText, "当前讲述方式：小余陪着问");
  assert.equal(context.data.projectTitle, "外婆回忆录");
  assert.equal(context.data.projectDisplayTitle, "外婆回忆录");
  assert.equal(context.data.hasProject, true);
  assert.deepEqual(transcriptCalls, [{ projectId: "project-1", limit: 1 }]);
  assert.equal(context.data.lastStoryHint, "爷爷退休后还在忙公司的事");
  assert.equal(context.data.lastStoryText, "上次您说到：爷爷退休后还在忙公司的事。");
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

test("home shows first-visit launch copy from launch state", async () => {
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
  assert.equal(context.data.showLaunchPanel, true);
  assert.equal(context.data.launchEyebrow, "聊出一本书");
  assert.equal(context.data.launchTitle, "我的故事，慢慢讲");
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
      showLaunchPanel: true,
    },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  page.dismissLaunchPanel.call(context);

  assert.deepEqual(dismissals, [["user-1", "new_project", "project-1"]]);
  assert.equal(context.data.launchVariant, "none");
  assert.equal(context.data.showLaunchPanel, false);
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
  assert.equal(createdSessions[0].input.metadata_json.entry, "miniprogram_home_resume");
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

test("home daily actions create free narration and human interview sessions", async () => {
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
  await page.startHumanInterview.call(context);

  assert.equal(createdSessions[0].input.guidance_mode, "free_narration");
  assert.equal(createdSessions[0].input.metadata_json.entry, "miniprogram_home_resume");
  assert.equal(createdSessions[0].input.metadata_json.resume_hint, "爷爷退休后还在忙公司的事");
  assert.equal(createdSessions[1].input.guidance_mode, "free_narration");
  assert.equal(createdSessions[1].input.interview_type, "human_interview");
  assert.equal(createdSessions[1].input.metadata_json.entry, "miniprogram_home_resume");
  assert.equal(createdSessions[1].input.metadata_json.interview_type, "human_interview");
  assert.equal(navigations.length, 0);
  assert.equal(context.data.voiceEntry.interviewType, "human_interview");
});

test("home routes book organization through the book workspace without requiring microphone consent", () => {
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
      projectId: "project/1",
      projectTitle: "爷爷的故事",
      projectDisplayTitle: "爷爷的故事",
      hasProject: true,
      voiceAuthorizationAgreed: false,
    },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  page.openBookWorkspace.call(context);

  const { takeBookWorkspaceNavigationTarget } = require("../services/book-workspace-navigation.ts");
  assert.deepEqual(navigations, ["/pages/book/index"]);
  assert.equal(takeBookWorkspaceNavigationTarget(), "chapters");
  assert.equal(context.data.selectedMode, "free_narration");
  assert.equal(context.data.error, "");

  context.data.creatingVoice = true;
  page.openBookWorkspace.call(context);
  page.openNarrationOverview.call(context);
  assert.deepEqual(navigations, ["/pages/book/index"], "navigation waits for voice preparation to finish");
});

test("home opens narration overview without requiring microphone consent", () => {
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
      projectId: "project/1",
      hasProject: true,
      voiceAuthorizationAgreed: false,
    },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  page.openNarrationOverview.call(context);

  assert.equal(navigations[0], "/pages/voice-progress/index");
  assert.equal(context.data.error, "");
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
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  await page.startPrimaryVoiceChat.call(context);

  assert.equal(createCalls, 0);
  assert.equal(context.data.error, "授权同步失败");
  assert.equal(context.data.creatingVoice, false);
});

test("home sends voice starts without a project to project creation", async () => {
  let createCalls = 0;
  const navigations = [];
  global.wx = {
    navigateTo(options) {
      navigations.push(options.url);
    },
  };
  const { page } = loadHomePage({
    voiceApiExports: {
      createMemoirVoiceSession: async () => {
        createCalls += 1;
        return { session: { id: "voice/skipped" } };
      },
    },
  });
  const context = {
    ...page,
    data: {
      ...page.data,
      needsLogin: false,
      projectId: "",
      hasProject: false,
      voiceAuthorizationAgreed: true,
    },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  await page.startPrimaryVoiceChat.call(context);

  assert.equal(createCalls, 0);
  assert.deepEqual(navigations, ["/pages/project-edit/index"]);
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
  const toasts = [];
  global.wx = {
    showToast(options) {
      toasts.push(options);
    },
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
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  await page.startPrimaryVoiceChat.call(context);

  assert.equal(createCalls, 0);
  assert.match(context.data.error, /声纹授权协议/);
  assert.equal(toasts[0].title, "请先同意声纹授权协议");
});

test("home refreshes voice authorization consent from storage", () => {
  let agreed = false;
  const { page } = loadHomePage({
    voiceAuthorizationExports: {
      readVoiceAuthorizationConsent: () => agreed,
    },
  });
  const context = {
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

test("home persists voice authorization checkbox changes", () => {
  let savedValue = null;
  const { page } = loadHomePage({
    voiceAuthorizationExports: {
      saveVoiceAuthorizationConsent: (agreed = true) => {
        savedValue = agreed;
        return {
          agreed,
          version: "test",
          agreedAt: "2026-06-18T00:00:00.000Z",
        };
      },
    },
  });
  const context = {
    data: { ...page.data, error: "旧错误" },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  page.changeVoiceAuthorization.call(context, {
    detail: { value: ["voice_authorization"] },
  });

  assert.equal(savedValue, true);
  assert.equal(context.data.voiceAuthorizationAgreed, true);
  assert.equal(context.data.error, "");
});
