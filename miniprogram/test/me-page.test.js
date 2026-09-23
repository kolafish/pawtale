require("./register-ts");

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const {
  mockMemoirApiModules,
  loadPageModule,
  mockModule,
  pageContext,
  unmockedAsync,
} = require("./page-harness");

function loadMePage({
  authExports = {},
  voiceApiExports = {},
  ttsExports = {},
  aiModelExports = {},
  accessibilityExports = {},
  voiceAuthorizationExports = {},
  profileExports = {},
  cacheExports = {},
  billingExports = {},
} = {}) {
  mockModule("../services/auth.ts", {
    getMemoirIdentity: unmockedAsync("getMemoirIdentity"),
    logoutBackendSession: async () => {},
    deleteAccountWithWeChat: unmockedAsync("deleteAccountWithWeChat"),
    updateBackendProfile: unmockedAsync("updateBackendProfile"),
    ...authExports,
  });
  mockMemoirApiModules({
    getMemoirVoiceProgress: async () => ({
      session_count: 0,
      utterance_count: 0,
      total_user_char_count: 0,
      total_audio_seconds: 0,
    }),
    ...voiceApiExports,
  });
  mockModule("../services/tts-settings.ts", {
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
    ...ttsExports,
  });
  mockModule("../services/ai-model-settings.ts", {
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
  });
  mockModule("../services/accessibility-settings.ts", {
    accessibilityPageClass: (baseClass, largeTextEnabled) => (
      largeTextEnabled ? `${baseClass} large-text` : baseClass
    ),
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
  });
  mockModule("../services/voice-authorization.ts", {
    clearVoiceAuthorizationConsentRecord: () => {},
    readVoiceAuthorizationConsentRecord: () => null,
    VOICE_AUTHORIZATION_PAGE_URL: "/pages/voice-authorization/index",
    VOICE_AUTHORIZATION_VERSION: "2026-06-18",
    ...voiceAuthorizationExports,
  });
  mockModule("../services/user-profile.ts", {
    resolveBackendAvatarUrl: (avatarUrl) => avatarUrl || "",
    uploadBackendAvatar: unmockedAsync("uploadBackendAvatar"),
    ...profileExports,
  });
  mockModule("../stores/memoir-read-cache.ts", {
    clearCurrentUserMemoirSnapshots: () => {},
    ...cacheExports,
  });
  mockModule("../services/billing.ts", {
    getAICreditSummary: async () => ({
      total_credit_rmb: 10,
      used_rmb: 0,
      remaining_rmb: 10,
    }),
    ...billingExports,
  });

  return loadPageModule("../pages/me/index.ts");
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

test("me shows AI credit usage percentage to one decimal place", async () => {
  const { page } = loadMePage({
    billingExports: {
      getAICreditSummary: async () => ({
        total_credit_rmb: 10,
        used_rmb: 0.03,
        remaining_rmb: 9.97,
      }),
    },
  });
  const context = pageContext(page);

  await page.loadAICreditSummary.call(context);

  assert.equal(context.data.creditProgressPercent, "0.3");
  assert.equal(context.data.creditUsedText, "0.03");
  assert.equal(context.data.creditRemainingText, "9.97");
});

test("me shows the single guest login entry when the session is missing", async () => {
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

test("me opens the dedicated login page from the guest entry", () => {
  const navigations = [];
  global.wx = {
    navigateTo(options) {
      navigations.push(options.url);
    },
  };
  const { page } = loadMePage();

  page.openLogin.call(pageContext(page));

  assert.deepEqual(navigations, ["/pages/login/index"]);
});

test("me profile exposes WeChat avatar and nickname controls", () => {
  const pageWxml = fs.readFileSync(
    path.join(__dirname, "../pages/me/index.wxml"),
    "utf8",
  );

  assert.match(pageWxml, /open-type="chooseAvatar"/);
  assert.match(pageWxml, /bindchooseavatar="chooseWeChatAvatar"/);
  assert.match(pageWxml, /type="nickname"/);
});

test("me saves the local large text preference", () => {
  const savedLargeText = [];
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
    },
  });
  const context = pageContext(page);

  page.refreshAccessibilitySettings.call(context);
  assert.equal(context.data.pageClass, "me-page large-text");
  assert.equal(context.data.largeTextEnabled, true);

  page.changeLargeText.call(context, { detail: { value: false } });

  assert.deepEqual(savedLargeText, [false]);
  assert.equal(context.data.largeTextEnabled, false);
  assert.equal(context.data.pageClass, "me-page");
  assert.deepEqual(toasts, ["大字模式已关闭"]);
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
  assert.equal(context.data.displayName, "微信昵称");
  assert.equal(context.data.avatarUrl, "https://avatar.example.com/wechat.png");
  assert.equal(context.data.accountEmailText, "微信账号登录");
});

test("me uploads a selected wechat avatar and refreshes the cached identity", async () => {
  const uploads = [];
  let identityRefreshes = 0;
  const toasts = [];
  global.wx = {
    showToast(options) {
      toasts.push(options.title);
    },
  };
  const { page } = loadMePage({
    authExports: {
      getMemoirIdentity: async () => {
        identityRefreshes += 1;
        return {
          id: "user-1",
          email: "wechat_x@wechat.local",
          password_login_enabled: false,
          display_name: "微信昵称",
          avatar_url: "/api/auth/avatars/user-1/avatar.jpg",
          created_at: "2026-07-24T00:00:00",
          active_project_id: "",
          active_project: null,
          projects: [],
        };
      },
    },
    profileExports: {
      resolveBackendAvatarUrl: (avatarUrl) => (
        avatarUrl?.startsWith("/") ? `https://api.example.com${avatarUrl}` : avatarUrl || ""
      ),
      uploadBackendAvatar: async (filePath) => {
        uploads.push(filePath);
        return {
          id: "user-1",
          email: "wechat_x@wechat.local",
          password_login_enabled: false,
          display_name: "微信昵称",
          avatar_url: "/api/auth/avatars/user-1/avatar.jpg",
          created_at: "2026-07-24T00:00:00",
        };
      },
    },
  });
  const context = pageContext(page, { loggedIn: true });

  await page.chooseWeChatAvatar.call(context, {
    detail: { avatarUrl: "wxfile://chosen-avatar" },
  });

  assert.deepEqual(uploads, ["wxfile://chosen-avatar"]);
  assert.equal(identityRefreshes, 1);
  assert.equal(
    context.data.avatarUrl,
    "https://api.example.com/api/auth/avatars/user-1/avatar.jpg",
  );
  assert.equal(context.data.avatarSaving, false);
  assert.deepEqual(toasts, ["头像已更新"]);
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
  assert.deepEqual(navigations, ["/pages/voice-authorization/index"]);
});

test("me shows missing voice authorization consent as not agreed", () => {
  const { page } = loadMePage();
  const context = pageContext(page, { voiceAuthorizationAgreed: true });

  page.refreshVoiceAuthorizationConsent.call(context);

  assert.equal(context.data.voiceAuthorizationAgreed, false);
  assert.equal(context.data.voiceAuthorizationStatusText, "未同意");
});


test("me keeps memoir management and voice settings as separate entries", () => {
  const navigations = [];
  global.wx = {
    navigateTo(options) {
      navigations.push(options.url);
    },
  };
  const { page } = loadMePage();
  const context = pageContext(page, { projectCount: 1 });

  page.openProjects.call(context);
  page.openVoiceSettings.call(context);

  assert.deepEqual(navigations, [
    "/pages/projects/index",
    "/pages/voice-settings/index",
  ]);
});

test("me clears the current user's memoir snapshots on logout", async () => {
  let cacheClearCount = 0;
  global.wx = {
    setStorageSync() {},
    showToast() {},
  };
  const { page } = loadMePage({
    cacheExports: {
      clearCurrentUserMemoirSnapshots: () => {
        cacheClearCount += 1;
      },
    },
  });
  const context = pageContext(page, {
    loading: false,
    loggedIn: true,
  });

  await page.logout.call(context);

  assert.equal(cacheClearCount, 1);
  assert.equal(context.data.loggedIn, false);
});

test("me keeps the active session when server logout fails", async () => {
  let cacheClearCount = 0;
  const { page } = loadMePage({
    authExports: { logoutBackendSession: async () => { throw new Error("网络不可用"); } },
    cacheExports: { clearCurrentUserMemoirSnapshots: () => { cacheClearCount += 1; } },
  });
  const context = pageContext(page, { loggedIn: true });

  await page.logout.call(context);

  assert.equal(context.data.loggedIn, true);
  assert.equal(context.data.loading, false);
  assert.equal(context.data.error, "网络不可用");
  assert.equal(cacheClearCount, 0);
});

test("me deletes the account only after confirmation and clears local user data", async () => {
  let cacheClearCount = 0;
  let consentClearCount = 0;
  let deleteCount = 0;
  global.wx = {
    setStorageSync() {},
    showModal(options) { options.success({ confirm: true }); },
    showToast() {},
  };
  const { page } = loadMePage({
    authExports: { deleteAccountWithWeChat: async () => { deleteCount += 1; } },
    cacheExports: { clearCurrentUserMemoirSnapshots: () => { cacheClearCount += 1; } },
    voiceAuthorizationExports: { clearVoiceAuthorizationConsentRecord: () => { consentClearCount += 1; } },
  });
  const context = pageContext(page, { loggedIn: true });

  await page.deleteAccount.call(context);

  assert.equal(deleteCount, 1);
  assert.equal(cacheClearCount, 1);
  assert.equal(consentClearCount, 1);
  assert.equal(context.data.loggedIn, false);
});

test("me keeps account and local data when deletion is cancelled or fails", async () => {
  let cacheClearCount = 0;
  let confirmed = false;
  global.wx = {
    showModal(options) { options.success({ confirm: confirmed }); },
  };
  const { page } = loadMePage({
    authExports: { deleteAccountWithWeChat: async () => { throw new Error("网络不可用"); } },
    cacheExports: { clearCurrentUserMemoirSnapshots: () => { cacheClearCount += 1; } },
  });
  const context = pageContext(page, { loggedIn: true });

  await page.deleteAccount.call(context);
  assert.equal(context.data.loggedIn, true);
  confirmed = true;
  await page.deleteAccount.call(context);
  assert.equal(context.data.loggedIn, true);
  assert.equal(context.data.error, "网络不可用");
  assert.equal(cacheClearCount, 0);
});
