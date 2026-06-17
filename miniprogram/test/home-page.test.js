require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

function loadHomePage({ authExports = {}, voiceApiExports = {} } = {}) {
  const authPath = require.resolve("../services/auth.ts");
  const voiceApiPath = require.resolve("../services/memoir-voice-api.ts");
  const homePath = require.resolve("../pages/home/index.ts");
  delete require.cache[authPath];
  delete require.cache[voiceApiPath];
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

test("home keeps incomplete wechat accounts on the home tab and asks for login", async () => {
  const { page } = loadHomePage({
    authExports: {
      getMemoirIdentity: async () => ({
        id: "user-1",
        email: "wechat_1234567890abcdef12345678@wechat.local",
        password_login_enabled: false,
        display_name: "微信用户",
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

  assert.equal(context.data.needsLogin, true);
  assert.equal(context.data.projectTitle, "我的回忆录");
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
  assert.equal(context.data.projectTitle, "外婆回忆录");
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
      createMemoirVoiceSession: async (input) => {
        createdSessions.push(input);
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
      selectedModule: "event",
    },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  await page.startVoiceChat.call(context);

  assert.equal(createdSessions.length, 1);
  assert.equal(createdSessions[0].guidance_mode, "guided_interview");
  assert.equal(createdSessions[0].current_guidance_module, "event");
  assert.equal(createdSessions[0].metadata_json.entry, "miniprogram_home");
  assert.equal(navigations[0], "/pages/voice-chat/index?voiceSessionId=voice%2F1&mode=guided_interview&module=event");
  assert.equal(context.data.creatingVoice, false);
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
    },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  await page.startVoiceChat.call(context);

  assert.deepEqual(switches, ["/pages/me/index"]);
  delete global.wx;
});
