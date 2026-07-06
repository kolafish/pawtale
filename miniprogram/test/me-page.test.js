require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

function loadMePage({
  authExports = {},
  voiceApiExports = {},
  ttsExports = {},
  aiModelExports = {},
  accessibilityExports = {},
  voiceAuthorizationExports = {},
} = {}) {
  const authPath = require.resolve("../services/auth.ts");
  const voiceApiPath = require.resolve("../services/memoir-voice-api.ts");
  const ttsPath = require.resolve("../services/tts-settings.ts");
  const aiModelPath = require.resolve("../services/ai-model-settings.ts");
  const accessibilityPath = require.resolve("../services/accessibility-settings.ts");
  const voiceAuthorizationPath = require.resolve("../services/voice-authorization.ts");
  const mePath = require.resolve("../pages/me/index.ts");
  delete require.cache[authPath];
  delete require.cache[voiceApiPath];
  delete require.cache[ttsPath];
  delete require.cache[aiModelPath];
  delete require.cache[accessibilityPath];
  delete require.cache[voiceAuthorizationPath];
  delete require.cache[mePath];
  require.cache[authPath] = {
    id: authPath,
    filename: authPath,
    loaded: true,
    exports: {
      isValidEmail: (email) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim()),
      getMemoirIdentity: async () => {
        throw new Error("getMemoirIdentity was not mocked");
      },
      loginWithEmail: async () => {
        throw new Error("loginWithEmail was not mocked");
      },
      loginWithWeChat: async () => {
        throw new Error("loginWithWeChat was not mocked");
      },
      logoutBackendSession: async () => {},
      setAccountCredentials: async () => {
        throw new Error("setAccountCredentials was not mocked");
      },
      updateBackendProfile: async () => {
        throw new Error("updateBackendProfile was not mocked");
      },
      ...authExports,
    },
  };
  require.cache[voiceApiPath] = {
    id: voiceApiPath,
    filename: voiceApiPath,
    loaded: true,
    exports: {
      getMemoirVoiceProgress: async () => ({
        session_count: 0,
        utterance_count: 0,
        total_user_char_count: 0,
        total_audio_seconds: 0,
      }),
      ...voiceApiExports,
    },
  };
  require.cache[ttsPath] = {
    id: ttsPath,
    filename: ttsPath,
    loaded: true,
    exports: {
      TTS_VOICE_OPTIONS: [
        {
          value: "",
          label: "默认音色",
          description: "使用后端当前默认配置",
        },
      ],
      TTS_SPEED_OPTIONS: [
        {
          value: 1,
          label: "正常 1.0x",
          description: "使用默认语速",
        },
      ],
      readTTSSettingsProfiles: () => ({
        chat: { voice: "", speedRatio: 1 },
        book: { voice: "", speedRatio: 1 },
      }),
      readScopedTTSSettings: () => ({ voice: "", speedRatio: 1 }),
      saveScopedTTSSettings: (_scope, settings) => settings,
      ttsSpeedLabel: () => "正常 1.0x",
      ttsVoiceLabel: () => "默认音色",
      ...ttsExports,
    },
  };
  require.cache[aiModelPath] = {
    id: aiModelPath,
    filename: aiModelPath,
    loaded: true,
    exports: {
      AI_MODEL_OPTIONS: [
        {
          value: "deepseek-v4-pro",
          label: "DeepSeek V4 Pro",
          description: "质量优先",
        },
        {
          value: "doubao-seed-2-0-pro-260215",
          label: "Doubao Seed 2.0 Pro",
          description: "豆包旗舰模型",
        },
      ],
      aiModelLabel: (model) => (
        model === "doubao-seed-2-0-pro-260215" ? "Doubao Seed 2.0 Pro" : "DeepSeek V4 Pro"
      ),
      readSelectedAIModel: () => "deepseek-v4-pro",
      saveSelectedAIModel: (model) => model,
      ...aiModelExports,
    },
  };
  require.cache[accessibilityPath] = {
    id: accessibilityPath,
    filename: accessibilityPath,
    loaded: true,
    exports: {
      readAccessibilitySettings: () => ({
        largeTextEnabled: false,
        autoBargeInEnabled: true,
      }),
      saveLargeTextEnabled: (largeTextEnabled) => ({
        largeTextEnabled,
        autoBargeInEnabled: true,
      }),
      saveAutoBargeInEnabled: (autoBargeInEnabled) => ({
        largeTextEnabled: false,
        autoBargeInEnabled,
      }),
      ...accessibilityExports,
    },
  };
  require.cache[voiceAuthorizationPath] = {
    id: voiceAuthorizationPath,
    filename: voiceAuthorizationPath,
    loaded: true,
    exports: {
      readVoiceAuthorizationConsentRecord: () => null,
      VOICE_AUTHORIZATION_PAGE_URL: "/pages/voice-authorization/index",
      VOICE_AUTHORIZATION_VERSION: "2026-06-18",
      ...voiceAuthorizationExports,
    },
  };

  let page = null;
  const previousPage = global.Page;
  global.Page = (definition) => {
    page = definition;
  };
  require(mePath);
  if (previousPage === undefined) {
    delete global.Page;
  } else {
    global.Page = previousPage;
  }
  return { page };
}

function pageContext(page, data = {}) {
  return {
    data: {
      ...page.data,
      ...data,
    },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
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

test("me shows the login panel instead of redirecting when the session is missing", async () => {
  const { ApiError } = require("../services/request.ts");
  const { page } = loadMePage({
    authExports: {
      getMemoirIdentity: async () => {
        throw new ApiError(401, "请先登录", {});
      },
    },
  });
  const context = pageContext(page);

  await page.loadMe.call(context);

  assert.equal(context.data.loggedIn, false);
  assert.equal(context.data.error, "");
  assert.equal(context.data.loading, false);
});

test("me saves local accessibility and voice environment switches", () => {
  const savedLargeText = [];
  const savedBargeIn = [];
  const toasts = [];
  global.wx = {
    showToast(options) {
      toasts.push(options.title);
    },
  };
  const { page } = loadMePage({
    accessibilityExports: {
      readAccessibilitySettings: () => ({
        largeTextEnabled: true,
        autoBargeInEnabled: false,
      }),
      saveLargeTextEnabled: (largeTextEnabled) => {
        savedLargeText.push(largeTextEnabled);
        return { largeTextEnabled, autoBargeInEnabled: false };
      },
      saveAutoBargeInEnabled: (autoBargeInEnabled) => {
        savedBargeIn.push(autoBargeInEnabled);
        return { largeTextEnabled: true, autoBargeInEnabled };
      },
    },
  });
  const context = pageContext(page);

  page.refreshAccessibilitySettings.call(context);
  assert.equal(context.data.largeTextEnabled, true);
  assert.equal(context.data.autoBargeInEnabled, false);

  page.changeLargeText.call(context, { detail: { value: false } });
  page.changeAutoBargeIn.call(context, { detail: { value: true } });

  assert.deepEqual(savedLargeText, [false]);
  assert.deepEqual(savedBargeIn, [true]);
  assert.equal(context.data.largeTextEnabled, true);
  assert.equal(context.data.autoBargeInEnabled, true);
  assert.deepEqual(toasts, ["大字模式已关闭", "自动检测已打开"]);
  delete global.wx;
});

test("me treats wechat-only users as logged in and shows wechat profile", async () => {
  const { page } = loadMePage({
    authExports: {
      getMemoirIdentity: async () => ({
        id: "user-1",
        email: "wechat_x@wechat.local",
        password_login_enabled: false,
        display_name: "微信昵称",
        avatar_url: "https://avatar.example.com/wechat.png",
        created_at: "2026-06-15T00:00:00",
        active_project_id: "",
        active_project: null,
        projects: [],
      }),
    },
  });
  const context = pageContext(page);

  await page.loadMe.call(context);

  assert.equal(context.data.loggedIn, true);
  assert.equal(context.data.passwordLoginEnabled, false);
  assert.equal(context.data.displayName, "微信昵称");
  assert.equal(context.data.avatarUrl, "https://avatar.example.com/wechat.png");
  assert.equal(context.data.accountEmailText, "微信账号登录");
  assert.equal(context.data.accountMode, false);
});

test("me keeps existing voice stats while refreshing the same project", async () => {
  const progress = deferred();
  let progressRequested = false;
  const progressProjectIds = [];
  const { page } = loadMePage({
    authExports: {
      getMemoirIdentity: async () => ({
        id: "user-1",
        email: "memoir-author@example.com",
        password_login_enabled: true,
        display_name: "作者",
        avatar_url: "",
        created_at: "2026-06-15T00:00:00",
        active_project_id: "project-1",
        active_project: {
          id: "project-1",
          title: "外婆回忆录",
          metadata_json: {},
        },
        projects: [],
      }),
    },
    voiceApiExports: {
      getMemoirVoiceProgress: async (projectId) => {
        progressRequested = true;
        progressProjectIds.push(projectId);
        return progress.promise;
      },
    },
  });
  const context = {
    ...page,
    ...pageContext(page, {
      loggedIn: true,
      voiceStatsProjectId: "project-1",
      sessionCount: 6,
      utteranceCount: 38,
      totalUserCharCount: 1200,
      totalAudioText: "18 分 2 秒",
    }),
  };

  const loading = page.loadMe.call(context);
  while (!progressRequested) {
    await new Promise((resolve) => setImmediate(resolve));
  }

  assert.equal(context.data.sessionCount, 6);
  assert.equal(context.data.utteranceCount, 38);
  assert.equal(context.data.totalUserCharCount, 1200);
  assert.equal(context.data.totalAudioText, "18 分 2 秒");
  assert.equal(context.data.voiceStatsLoading, true);
  assert.deepEqual(progressProjectIds, ["project-1"]);

  progress.resolve({
    session_count: 7,
    utterance_count: 42,
    total_user_char_count: 1500,
    total_audio_seconds: 1200,
  });
  await loading;

  assert.equal(context.data.sessionCount, 7);
  assert.equal(context.data.utteranceCount, 42);
  assert.equal(context.data.totalUserCharCount, 1500);
  assert.equal(context.data.totalAudioText, "20 分 0 秒");
  assert.equal(context.data.voiceStatsLoading, false);
});

test("me loads voice stats only for the active project", async () => {
  const progressProjectIds = [];
  const { page } = loadMePage({
    authExports: {
      getMemoirIdentity: async () => ({
        id: "user-1",
        email: "memoir-author@example.com",
        password_login_enabled: true,
        display_name: "作者",
        avatar_url: "",
        created_at: "2026-06-15T00:00:00",
        active_project_id: "project-current",
        active_project: {
          id: "project-current",
          title: "当前回忆录",
          metadata_json: {},
        },
        projects: [
          { id: "project-current", title: "当前回忆录" },
          { id: "project-other", title: "其他回忆录" },
        ],
      }),
    },
    voiceApiExports: {
      getMemoirVoiceProgress: async (projectId) => {
        progressProjectIds.push(projectId);
        return {
          session_count: 4,
          utterance_count: 16,
          total_user_char_count: 640,
          total_audio_seconds: 360,
        };
      },
    },
  });
  const context = {
    ...page,
    ...pageContext(page),
  };

  await page.loadMe.call(context);

  assert.deepEqual(progressProjectIds, ["project-current"]);
  assert.equal(context.data.projectTitle, "当前回忆录");
  assert.equal(context.data.projectCount, 2);
  assert.equal(context.data.voiceStatsProjectId, "project-current");
  assert.equal(context.data.sessionCount, 4);
  assert.equal(context.data.totalAudioText, "6 分 0 秒");
});

test("me shows voice authorization status and opens agreement", () => {
  const navigations = [];
  global.wx = {
    navigateTo(options) {
      navigations.push(options.url);
    },
  };
  const { page } = loadMePage({
    voiceAuthorizationExports: {
      readVoiceAuthorizationConsentRecord: () => ({
        agreed: true,
        version: "2026-06-18",
        agreedAt: "2026-06-18T00:00:00.000Z",
      }),
    },
  });
  const context = pageContext(page);

  page.refreshVoiceAuthorizationConsent.call(context);
  page.openVoiceAuthorization.call(context);

  assert.equal(context.data.voiceAuthorizationAgreed, true);
  assert.equal(context.data.voiceAuthorizationStatusText, "已同意");
  assert.match(context.data.voiceAuthorizationDetailText, /06-18 08:00 北京时间 已同意/);
  assert.match(context.data.voiceAuthorizationDetailText, /协议版本 2026-06-18/);
  assert.deepEqual(navigations, ["/pages/voice-authorization/index"]);
  delete global.wx;
});

test("me shows missing voice authorization consent as not agreed", () => {
  const { page } = loadMePage();
  const context = pageContext(page, { voiceAuthorizationAgreed: true });

  page.refreshVoiceAuthorizationConsent.call(context);

  assert.equal(context.data.voiceAuthorizationAgreed, false);
  assert.equal(context.data.voiceAuthorizationStatusText, "未同意");
  assert.match(context.data.voiceAuthorizationDetailText, /开始语音前/);
});

test("me refreshes profile after wechat login", async () => {
  let refreshed = false;
  const { page } = loadMePage({
    authExports: {
      loginWithWeChat: async () => ({
        user: {
          id: "user-1",
          email: "wechat_x@wechat.local",
          password_login_enabled: false,
          display_name: "微信昵称",
          created_at: "2026-06-15T00:00:00",
        },
        needs_credentials: true,
      }),
    },
  });
  global.wx = {
    showToast() {},
  };
  const context = {
    ...pageContext(page),
    async loadMe() {
      refreshed = true;
      this.setData({ loggedIn: true });
    },
  };

  await page.submitWeChat.call(context);

  assert.equal(refreshed, true);
  assert.equal(context.data.loggedIn, true);
  assert.equal(context.data.wechatLoading, false);
  delete global.wx;
});

test("me validates email before setting Web login credentials", async () => {
  let credentialsCalls = 0;
  const { page } = loadMePage({
    authExports: {
      setAccountCredentials: async () => {
        credentialsCalls += 1;
        return {
          user: {
            id: "user-1",
            email: "author@example.com",
            password_login_enabled: true,
            display_name: "作者",
            created_at: "2026-06-15T00:00:00",
          },
          needs_credentials: false,
        };
      },
    },
  });
  const context = pageContext(page, {
    credentialEmail: "author",
    credentialPassword: "password123",
  });

  await page.submitCredentials.call(context);

  assert.equal(credentialsCalls, 0);
  assert.match(context.data.error, /邮箱/);
});

test("me saves Web login credentials without overriding wechat nickname", async () => {
  const credentialsCalls = [];
  let refreshed = false;
  const { page } = loadMePage({
    authExports: {
      setAccountCredentials: async (input) => {
        credentialsCalls.push(input);
        return {
          user: {
            id: "user-1",
            email: input.email,
            password_login_enabled: true,
            display_name: "微信昵称",
            created_at: "2026-06-15T00:00:00",
          },
          needs_credentials: false,
        };
      },
    },
  });
  global.wx = {
    showToast() {},
  };
  const context = {
    ...pageContext(page, {
      credentialEmail: " author@example.com ",
      credentialPassword: "password123",
    }),
    async loadMe() {
      refreshed = true;
      this.setData({ loggedIn: true, passwordLoginEnabled: true });
    },
  };

  await page.submitCredentials.call(context);

  assert.deepEqual(credentialsCalls, [
    {
      email: "author@example.com",
      password: "password123",
    },
  ]);
  assert.equal(refreshed, true);
  assert.equal(context.data.passwordLoginEnabled, true);
  delete global.wx;
});

test("me refreshes profile after Pawtale account login", async () => {
  const loginCalls = [];
  let refreshed = false;
  const { page } = loadMePage({
    authExports: {
      loginWithEmail: async (input) => {
        loginCalls.push(input);
        return {
          id: "user-1",
          email: input.email,
          password_login_enabled: true,
          display_name: "作者",
          created_at: "2026-06-15T00:00:00",
        };
      },
    },
  });
  global.wx = {
    showToast() {},
  };
  const context = {
    ...pageContext(page, {
      emailInput: " author@example.com ",
      password: "password123",
    }),
    async loadMe() {
      refreshed = true;
      this.setData({ loggedIn: true });
    },
  };

  await page.submitAccount.call(context);

  assert.deepEqual(loginCalls, [
    {
      email: "author@example.com",
      password: "password123",
    },
  ]);
  assert.equal(refreshed, true);
  assert.equal(context.data.loggedIn, true);
  assert.equal(context.data.loading, false);
  delete global.wx;
});

test("me saves chat and book TTS settings independently", () => {
  let profiles = {
    chat: {
      voice: "zh_female_vv_uranus_bigtts",
      speedRatio: 0.7,
    },
    book: {
      voice: "",
      speedRatio: 1,
    },
  };
  const saved = [];
  const { page } = loadMePage({
    ttsExports: {
      readTTSSettingsProfiles: () => profiles,
      readScopedTTSSettings: (scope) => profiles[scope],
      saveScopedTTSSettings: (scope, nextSettings) => {
        profiles = {
          ...profiles,
          [scope]: nextSettings,
        };
        saved.push({ scope, settings: nextSettings });
        return nextSettings;
      },
      ttsVoiceLabel: (voice) => (voice ? "通用男声" : "默认音色"),
      ttsSpeedLabel: (speedRatio) => `${speedRatio}x`,
    },
  });
  const toastCalls = [];
  global.wx = {
    showToast(input) {
      toastCalls.push(input);
    },
  };
  const context = pageContext(page, {
    chatTTSVoice: profiles.chat.voice,
    chatTTSSpeedRatio: profiles.chat.speedRatio,
    bookTTSVoice: profiles.book.voice,
    bookTTSSpeedRatio: profiles.book.speedRatio,
  });

  page.selectTTSVoice.call(context, {
    currentTarget: {
      dataset: {
        scope: "book",
        voice: "zh_male_m191_uranus_bigtts",
      },
    },
  });
  page.selectTTSSpeed.call(context, {
    currentTarget: {
      dataset: {
        scope: "chat",
        speed: "1.5",
      },
    },
  });

  assert.deepEqual(saved, [
    {
      scope: "book",
      settings: {
        voice: "zh_male_m191_uranus_bigtts",
        speedRatio: 1,
      },
    },
    {
      scope: "chat",
      settings: {
        voice: "zh_female_vv_uranus_bigtts",
        speedRatio: 1.5,
      },
    },
  ]);
  assert.equal(context.data.bookTTSVoice, "zh_male_m191_uranus_bigtts");
  assert.equal(context.data.bookTTSSpeedRatio, 1);
  assert.equal(context.data.chatTTSVoice, "zh_female_vv_uranus_bigtts");
  assert.equal(context.data.chatTTSSpeedRatio, 1.5);
  assert.equal(context.data.chatTTSSpeedLabel, "1.5x");
  assert.equal(toastCalls.length, 2);
  delete global.wx;
});

test("me saves selected AI model", () => {
  const saved = [];
  const { page } = loadMePage({
    aiModelExports: {
      saveSelectedAIModel: (model) => {
        saved.push(model);
        return model === "doubao-seed-2-0-pro-260215" ? model : "deepseek-v4-pro";
      },
    },
  });
  const toastCalls = [];
  global.wx = {
    showToast(input) {
      toastCalls.push(input);
    },
  };
  const context = pageContext(page);

  page.selectAIModel.call(context, {
    currentTarget: {
      dataset: {
        model: "doubao-seed-2-0-pro-260215",
      },
    },
  });

  assert.deepEqual(saved, ["doubao-seed-2-0-pro-260215"]);
  assert.equal(context.data.aiModel, "doubao-seed-2-0-pro-260215");
  assert.equal(context.data.aiModelLabel, "Doubao Seed 2.0 Pro");
  assert.equal(toastCalls.length, 1);
  delete global.wx;
});

test("me keeps memoir management, voice progress, and debug speaker tools as separate entries", () => {
  const navigations = [];
  global.wx = {
    navigateTo(options) {
      navigations.push(options.url);
    },
  };
  const { page } = loadMePage();
  const context = pageContext(page);

  page.openVoiceProgress.call(context);
  page.openProjects.call(context);
  page.openDebugSpeakerTranscribe.call(context);

  assert.deepEqual(navigations, [
    "/pages/voice-progress/index",
    "/pages/projects/index",
    "/pages/debug-speaker-transcribe/index",
  ]);
  delete global.wx;
});
