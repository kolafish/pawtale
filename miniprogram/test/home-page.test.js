require("./register-ts");

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

function loadHomePage({ authExports = {}, voiceApiExports = {}, voiceAuthorizationExports = {} } = {}) {
  const authPath = require.resolve("../services/auth.ts");
  const voiceApiPath = require.resolve("../services/memoir-voice-api.ts");
  const voiceAuthorizationPath = require.resolve("../services/voice-authorization.ts");
  const homePath = require.resolve("../pages/home/index.ts");
  delete require.cache[authPath];
  delete require.cache[voiceApiPath];
  delete require.cache[voiceAuthorizationPath];
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
      VOICE_AUTHORIZATION_PAGE_URL: "/pages/voice-authorization/index",
      ...voiceAuthorizationExports,
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
  });
  const context = {
    data: { ...page.data },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  await page.loadHome.call(context);

  assert.equal(context.data.selectedMode, "guided_interview");
  assert.equal(context.data.guidanceLabel, "主题讲述");
  assert.equal(context.data.sessionGuidanceLabel, "AI 引导 · 人物");
  assert.equal(context.data.showGuidanceModules, true);
  assert.equal(context.data.projectTitle, "外婆回忆录");
  assert.equal(context.data.hasProject, true);
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

test("home creates voice sessions with the project-bound guidance mode", async () => {
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
            id: "voice/1",
          },
        };
      },
    },
  });
  const context = {
    data: {
      ...page.data,
      selectedMode: "guided_interview",
      selectedModule: "place",
      projectId: "project/1",
      hasProject: true,
      voiceAuthorizationAgreed: true,
    },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  await page.startVoiceChat.call(context);

  assert.equal(createdSessions.length, 1);
  assert.equal(createdSessions[0].input.guidance_mode, "guided_interview");
  assert.equal(createdSessions[0].input.current_guidance_module, "place");
  assert.equal(createdSessions[0].input.metadata_json.entry, "miniprogram_home");
  assert.deepEqual(createdSessions[0].options, { projectId: "project/1" });
  assert.equal(navigations[0], "/pages/voice-chat/index?voiceSessionId=voice%2F1&mode=guided_interview&module=place");
  assert.equal(context.data.creatingVoice, false);
  delete global.wx;
});

test("home updates the guided module selection for this voice session", () => {
  const { page } = loadHomePage();
  const context = {
    data: { ...page.data, selectedMode: "guided_interview", selectedModule: "person" },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  page.selectGuidanceModule.call(context, {
    currentTarget: {
      dataset: {
        module: "theme",
      },
    },
  });

  assert.equal(context.data.selectedMode, "guided_interview");
  assert.equal(context.data.selectedModule, "theme");
  assert.equal(context.data.selectedModuleLabel, "主题");
  assert.equal(context.data.sessionGuidanceLabel, "AI 引导 · 主题");
  assert.equal(context.data.showGuidanceModules, true);
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
    data: {
      ...page.data,
      selectedMode: "free_narration",
      selectedModule: "theme",
      projectId: "project/1",
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
  assert.equal(navigations[0], "/pages/voice-chat/index?voiceSessionId=voice%2Ffree&mode=free_narration");
  delete global.wx;
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
