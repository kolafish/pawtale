require("./register-ts");

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const {
  memoirHomePrompt,
  memoirStoryTitle,
} = require("../services/memoir-display-copy.ts");

function loadHomePage({
  authExports = {},
  voiceApiExports = {},
  voiceAuthorizationExports = {},
  launchStateExports = {},
} = {}) {
  const authPath = require.resolve("../services/auth.ts");
  const voiceApiPath = require.resolve("../services/memoir-voice-api.ts");
  const voiceAuthorizationPath = require.resolve("../services/voice-authorization.ts");
  const launchStatePath = require.resolve("../services/home-launch-state.ts");
  const homePath = require.resolve("../pages/home/index.ts");
  delete require.cache[authPath];
  delete require.cache[voiceApiPath];
  delete require.cache[voiceAuthorizationPath];
  delete require.cache[launchStatePath];
  delete require.cache[homePath];
  require.cache[authPath] = {
    id: authPath,
    filename: authPath,
    loaded: true,
    exports: {
      getMemoirIdentity: async () => {
        throw new Error("getMemoirIdentity was not mocked");
      },
      logoutBackendSession: async () => {},
      ...authExports,
    },
  };
  require.cache[voiceApiPath] = {
    id: voiceApiPath,
    filename: voiceApiPath,
    loaded: true,
    exports: {
      createMemoirVoiceSession: async () => {
        throw new Error("createMemoirVoiceSession was not mocked");
      },
      listMemoirVoiceTranscripts: async () => ({ items: [], total: 0, limit: 1, offset: 0 }),
      ...voiceApiExports,
    },
  };
  require.cache[voiceAuthorizationPath] = {
    id: voiceAuthorizationPath,
    filename: voiceAuthorizationPath,
    loaded: true,
    exports: {
      readVoiceAuthorizationConsent: () => true,
      saveVoiceAuthorizationConsent: (agreed = true) => ({
        agreed,
        version: "test",
        agreedAt: "2026-06-18T00:00:00.000Z",
      }),
      syncVoiceAuthorizationConsent: async () => ({ id: "consent-1" }),
      VOICE_AUTHORIZATION_PAGE_URL: "/pages/voice-authorization/index",
      ...voiceAuthorizationExports,
    },
  };
  require.cache[launchStatePath] = {
    id: launchStatePath,
    filename: launchStatePath,
    loaded: true,
    exports: {
      dismissHomeLaunch: () => {},
      resolveHomeLaunch: () => "none",
      ...launchStateExports,
    },
  };

  let page = null;
  const previousPage = global.Page;
  global.Page = (definition) => {
    page = definition;
  };
  const mod = require(homePath);
  if (previousPage === undefined) {
    delete global.Page;
  } else {
    global.Page = previousPage;
  }
  return { mod, page };
}

test("mini program opens on the browseable home tab", () => {
  const appJson = JSON.parse(fs.readFileSync(path.join(__dirname, "../app.json"), "utf8"));

  assert.equal(appJson.pages[0], "pages/home/index");
});

test("home only shows voice authorization panel before current consent", () => {
  const wxml = fs.readFileSync(path.join(__dirname, "../pages/home/index.wxml"), "utf8");

  assert.match(wxml, /wx:if="{{showVoiceAuthorizationPanel}}" class="voice-auth-panel"/);
});

test("home keeps voice entry free of per-session guidance controls", () => {
  const wxml = fs.readFileSync(path.join(__dirname, "../pages/home/index.wxml"), "utf8");

  assert.equal(wxml.includes("本次讲述方式"), false);
  assert.equal(wxml.includes("guidance-options"), false);
  assert.equal(wxml.includes("module-options"), false);
  assert.equal(wxml.includes("voice-mode-row"), false);
});

test("home uses one warm memoir card with a clear voice start button", () => {
  const wxml = fs.readFileSync(path.join(__dirname, "../pages/home/index.wxml"), "utf8");
  const wxss = fs.readFileSync(path.join(__dirname, "../pages/home/index.wxss"), "utf8");

  assert.match(wxml, /class="home-card"/);
  assert.equal(wxml.includes('class="project-panel"'), false);
  assert.equal(wxml.includes('class="voice-panel"'), false);
  assert.match(wxml, /class="{{voiceStartButtonClass}}"/);
  assert.match(wxml, /{{panelLabelText}}/);
  assert.match(wxml, /{{modeTagText}}/);
  assert.match(wxml, /{{projectMetaText}}/);
  assert.match(wxml, /{{primaryVoiceButtonTitle}}/);
  assert.match(wxml, /{{voiceStartNoteText}}/);
  assert.equal(wxml.includes('loading ? "加载中"'), false);
  assert.equal(wxml.includes('loading ? "我先确认当前回忆录。"'), false);
  assert.equal(wxml.includes('loading ? "正在加载"'), false);
  assert.equal(wxml.includes('loading ? "马上就好。"'), false);
  assert.equal(wxss.includes("#2f7d63"), false);
  assert.match(wxss, /\.voice-start-button[\s\S]*#a64b3e/);
  assert.match(wxss, /\.voice-start-button[\s\S]*border-radius: 999rpx/);
  assert.match(wxss, /\.voice-start-button\.button-blocked[\s\S]*background: #a64b3e/);
  assert.equal(wxml.includes('disabled="{{loading || creatingVoice}}"'), false);
  assert.match(wxss, /\.voice-start-title[\s\S]*#ffffff/);
});

test("home polish keeps surfaces tactile without broad transitions", () => {
  const wxml = fs.readFileSync(path.join(__dirname, "../pages/home/index.wxml"), "utf8");
  const wxss = fs.readFileSync(path.join(__dirname, "../pages/home/index.wxss"), "utf8");

  assert.equal(wxss.includes("transition: all"), false);
  assert.equal(wxss.includes("text-wrap:"), false);
  assert.equal(wxss.includes("font-smoothing"), false);
  assert.match(wxss, /\.home-card,[\s\S]*box-shadow:\s*\n\s*0 0 0 1rpx/);
  assert.match(wxss, /\.voice-start-button-hover[\s\S]*transform: scale\(0\.96\)/);
  assert.match(wxss, /\.resume-ask-button-hover[\s\S]*transform: scale\(0\.96\)/);
  assert.match(wxss, /\.launch-secondary-button-hover[\s\S]*transform: scale\(0\.96\)/);
  assert.match(wxss, /\.return-prompt-close[\s\S]*width: 84rpx/);
  assert.match(wxml, /hover-class="{{voiceStartButtonHoverClass}}"/);
  assert.match(wxml, /hover-class="{{resumeAskButtonHoverClass}}"/);
  assert.equal(wxml.includes("loading || creatingVoice"), false);
  assert.match(wxml, /<view class="return-prompt-close"[^>]*hover-class="return-prompt-close-hover"/);
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
    guidanceLabel: "慢慢讲",
    voiceAuthorizationAgreed: true,
  };

  assert.deepEqual(
    {
      greetingText: homeViewPatch(baseData).greetingText,
      launchModeTagText: homeViewPatch(baseData).launchModeTagText,
      panelLabelText: homeViewPatch(baseData).panelLabelText,
      projectHeadingText: homeViewPatch(baseData).projectHeadingText,
      projectMetaText: homeViewPatch(baseData).projectMetaText,
      primaryVoiceButtonTitle: homeViewPatch(baseData).primaryVoiceButtonTitle,
      voiceStartButtonClass: homeViewPatch({ ...baseData, creatingVoice: true }).voiceStartButtonClass,
      voiceStartButtonHoverClass: homeViewPatch({ ...baseData, creatingVoice: true }).voiceStartButtonHoverClass,
      showResumeActions: homeViewPatch(baseData).showResumeActions,
      showVoiceAuthorizationPanel: homeViewPatch(baseData).showVoiceAuthorizationPanel,
    },
    {
      greetingText: "你好，余主任",
      launchModeTagText: "新故事本",
      panelLabelText: "今天接着聊",
      projectHeadingText: "爷爷的故事",
      projectMetaText: "上次您说到：爷爷退休后还在忙公司的事。",
      primaryVoiceButtonTitle: "我来说一段",
      voiceStartButtonClass: "voice-start-button button-blocked",
      voiceStartButtonHoverClass: "none",
      showResumeActions: true,
      showVoiceAuthorizationPanel: false,
    },
  );
});

test("home userDisplayName hides wechat placeholder display name", () => {
  const { userDisplayName } = loadHomePage().mod;

  assert.equal(userDisplayName("微信用户", "memoir-author@example.com"), "memoir-author");
});

test("home userDisplayName hides provisional wechat local email", () => {
  const { userDisplayName } = loadHomePage().mod;

  assert.equal(userDisplayName("微信用户", "wechat_1234567890abcdef12345678@wechat.local"), "作者");
});

test("home userDisplayName keeps real display names", () => {
  const { userDisplayName } = loadHomePage().mod;

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
    data: { ...page.data },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  await page.loadHome.call(context);

  assert.equal(context.data.selectedMode, "guided_interview");
  assert.equal(context.data.guidanceLabel, "小余陪着问");
  assert.equal(context.data.projectTitle, "外婆回忆录");
  assert.equal(context.data.projectDisplayTitle, "外婆回忆录");
  assert.equal(context.data.hasProject, true);
  assert.deepEqual(transcriptCalls, [{ projectId: "project-1", limit: 1 }]);
  assert.equal(context.data.lastStoryHint, "爷爷退休后还在忙公司的事");
  assert.equal(context.data.lastStoryText, "上次您说到：爷爷退休后还在忙公司的事。");
  assert.equal(context.data.hasLastStoryHint, true);
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

  await page.startVoiceChat.call(context);

  assert.equal(createdSessions.length, 1);
  assert.equal(syncedConsents.length, 1);
  assert.equal(createdSessions[0].input.guidance_mode, "guided_interview");
  assert.equal(createdSessions[0].input.current_guidance_module, undefined);
  assert.equal(createdSessions[0].input.metadata_json.entry, "miniprogram_home");
  assert.deepEqual(createdSessions[0].options, { projectId: "project/1" });
  assert.equal(navigations[0], "/pages/voice-chat/index?voiceSessionId=voice%2F1&mode=guided_interview&projectTitle=%E7%88%B7%E7%88%B7%E7%9A%84%E6%95%85%E4%BA%8B");
  assert.equal(context.data.creatingVoice, false);
  delete global.wx;
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

  await page.startVoiceChat.call(context);

  assert.equal(createdSessions.length, 1);
  assert.equal(createdSessions[0].input.guidance_mode, "free_narration");
  assert.equal(createdSessions[0].input.current_guidance_module, undefined);
  assert.equal(navigations[0], "/pages/voice-chat/index?voiceSessionId=voice%2Ffree&mode=free_narration&projectTitle=%E8%87%AA%E7%94%B1%E5%9B%9E%E5%BF%86");
  delete global.wx;
});

test("home daily actions create free narration and guided interview sessions", async () => {
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

  await page.startFreeNarration.call(context);
  await page.startGuidedInterview.call(context);

  assert.equal(createdSessions[0].input.guidance_mode, "free_narration");
  assert.equal(createdSessions[0].input.metadata_json.entry, "miniprogram_home_resume");
  assert.equal(createdSessions[0].input.metadata_json.resume_hint, "爷爷退休后还在忙公司的事");
  assert.equal(createdSessions[1].input.guidance_mode, "guided_interview");
  assert.equal(createdSessions[1].input.metadata_json.entry, "miniprogram_home_resume");
  assert.equal(navigations.length, 2);
  delete global.wx;
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

  await page.startVoiceChat.call(context);

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

  await page.startVoiceChat.call(context);

  assert.equal(createCalls, 0);
  assert.deepEqual(navigations, ["/pages/project-edit/index"]);
  delete global.wx;
});

test("home sends login-required voice starts to the me tab", async () => {
  const switches = [];
  global.wx = {
    switchTab(options) {
      switches.push(options.url);
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

  await page.startVoiceChat.call(context);

  assert.deepEqual(switches, ["/pages/me/index"]);
  delete global.wx;
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

  await page.startVoiceChat.call(context);

  assert.equal(createCalls, 0);
  assert.match(context.data.error, /声纹授权协议/);
  assert.equal(toasts[0].title, "请先同意声纹授权协议");
  delete global.wx;
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
