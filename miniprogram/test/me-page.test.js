require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

function loadMePage({ authExports = {}, voiceApiExports = {}, ttsExports = {} } = {}) {
  const authPath = require.resolve("../services/auth.ts");
  const voiceApiPath = require.resolve("../services/memoir-voice-api.ts");
  const ttsPath = require.resolve("../services/tts-settings.ts");
  const mePath = require.resolve("../pages/me/index.ts");
  delete require.cache[authPath];
  delete require.cache[voiceApiPath];
  delete require.cache[ttsPath];
  delete require.cache[mePath];
  require.cache[authPath] = {
    id: authPath,
    filename: authPath,
    loaded: true,
    exports: {
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
  assert.equal(context.data.credentialsMode, false);
  assert.equal(context.data.error, "");
  assert.equal(context.data.loading, false);
});

test("me keeps new wechat users in credential setup mode", async () => {
  const { page } = loadMePage({
    authExports: {
      loginWithWeChat: async () => ({
        user: {
          id: "user-1",
          email: "wechat_x@wechat.local",
          password_login_enabled: false,
          display_name: "微信用户",
          created_at: "2026-06-15T00:00:00",
        },
        needs_credentials: true,
      }),
    },
  });
  const context = pageContext(page);

  await page.submitWeChat.call(context);

  assert.equal(context.data.loggedIn, false);
  assert.equal(context.data.credentialsMode, true);
  assert.equal(context.data.accountMode, false);
  assert.equal(context.data.credentialDisplayName, "");
  assert.equal(context.data.wechatLoading, false);
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
