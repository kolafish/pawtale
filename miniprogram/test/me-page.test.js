require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

function loadMePage({ authExports = {}, voiceApiExports = {}, ttsExports = {}, aiModelExports = {} } = {}) {
  const authPath = require.resolve("../services/auth.ts");
  const voiceApiPath = require.resolve("../services/memoir-voice-api.ts");
  const ttsPath = require.resolve("../services/tts-settings.ts");
  const aiModelPath = require.resolve("../services/ai-model-settings.ts");
  const mePath = require.resolve("../pages/me/index.ts");
  delete require.cache[authPath];
  delete require.cache[voiceApiPath];
  delete require.cache[ttsPath];
  delete require.cache[aiModelPath];
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
