require("./register-ts");

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const {
  loadPageModule,
  mockModule,
  pageContext,
  unmockedAsync,
} = require("./page-harness");
const { hasUsableDisplayName, needsWeChatProfileSetup } = require("../services/auth.ts");

function loadLoginPage({ authExports = {}, profileExports = {} } = {}) {
  mockModule("../services/auth.ts", {
    hasUsableDisplayName,
    needsWeChatProfileSetup,
    loginWithWeChat: unmockedAsync("loginWithWeChat"),
    updateBackendProfile: unmockedAsync("updateBackendProfile"),
    ...authExports,
  });
  mockModule("../services/user-profile.ts", {
    resolveBackendAvatarUrl: (avatarUrl) => avatarUrl || "",
    uploadBackendAvatar: unmockedAsync("uploadBackendAvatar"),
    ...profileExports,
  });

  return loadPageModule("../pages/login/index.ts");
}

test("login profile setup uses WeChat avatar and nickname components", () => {
  const template = fs.readFileSync(
    path.join(__dirname, "../pages/login/index.wxml"),
    "utf8",
  );

  assert.match(template, /open-type="chooseAvatar"/);
  assert.match(template, /bindchooseavatar="chooseWeChatAvatar"/);
  assert.match(template, /type="nickname"/);
});

test("login page offers WeChat as its only sign-in action", () => {
  const template = fs.readFileSync(path.join(__dirname, "../pages/login/index.wxml"), "utf8");
  assert.match(template, /bindtap="submitWeChat"/);
  assert.doesNotMatch(template, /bindtap="submitAccount"|account-login-toggle/);
});

test("login page relaunches home when the wechat profile is already complete", async () => {
  const relaunches = [];
  global.wx = {
    reLaunch(options) {
      relaunches.push(options.url);
    },
  };
  const { page } = loadLoginPage({
    authExports: {
      loginWithWeChat: async () => ({
        user: {
          id: "user-1",
          email: "wechat_x@wechat.local",
          password_login_enabled: false,
          display_name: "微信昵称",
          avatar_url: "https://avatar.example.com/wechat.png",
          created_at: "2026-06-15T00:00:00",
        },
        needs_credentials: true,
      }),
    },
  });
  const context = pageContext(page);

  await page.submitWeChat.call(context);

  assert.deepEqual(relaunches, ["/pages/home/index"]);
  assert.equal(context.data.wechatLoading, false);
});

test("login page asks a new wechat user to choose nickname and avatar", async () => {
  const relaunches = [];
  global.wx = {
    reLaunch(options) {
      relaunches.push(options.url);
    },
  };
  const { page } = loadLoginPage({
    authExports: {
      loginWithWeChat: async () => ({
        user: {
          id: "user-new",
          email: "wechat_new@wechat.local",
          password_login_enabled: false,
          display_name: "微信用户",
          avatar_url: null,
          created_at: "2026-07-24T00:00:00",
        },
        needs_credentials: true,
      }),
    },
  });
  const context = pageContext(page);

  await page.submitWeChat.call(context);

  assert.deepEqual(relaunches, []);
  assert.equal(context.data.profileMode, true);
  assert.equal(context.data.profileDisplayName, "");
  assert.equal(context.data.profileAvatarPath, "");
  assert.equal(context.data.wechatLoading, false);
});

test("login page saves selected wechat nickname and avatar before entering", async () => {
  const profileUpdates = [];
  const avatarUploads = [];
  const relaunches = [];
  global.wx = {
    reLaunch(options) {
      relaunches.push(options.url);
    },
  };
  const { page } = loadLoginPage({
    authExports: {
      updateBackendProfile: async (input) => {
        profileUpdates.push(input);
        return {
          id: "user-new",
          email: "wechat_new@wechat.local",
          password_login_enabled: false,
          display_name: input.display_name,
          created_at: "2026-07-24T00:00:00",
        };
      },
    },
    profileExports: {
      uploadBackendAvatar: async (filePath) => {
        avatarUploads.push(filePath);
        return {
          id: "user-new",
          email: "wechat_new@wechat.local",
          password_login_enabled: false,
          display_name: "微信昵称",
          avatar_url: "/api/auth/avatars/user-new/avatar.jpg",
          created_at: "2026-07-24T00:00:00",
        };
      },
    },
  });
  const context = pageContext(page, {
    profileMode: true,
    profileDisplayName: " 微信昵称 ",
    profileAvatarPath: "wxfile://chosen-avatar",
  });

  await page.saveWeChatProfile.call(context);

  assert.deepEqual(profileUpdates, [{ display_name: "微信昵称" }]);
  assert.deepEqual(avatarUploads, ["wxfile://chosen-avatar"]);
  assert.deepEqual(relaunches, ["/pages/home/index"]);
  assert.equal(context.data.profileSaving, false);
});

test("a first like asks for avatar and nickname, then returns to what was liked", async () => {
  const backs = [];
  const relaunches = [];
  global.wx = {
    navigateBack: () => backs.push(true),
    reLaunch: ({ url }) => relaunches.push(url),
  };
  const { page } = loadLoginPage();
  const context = pageContext(page);

  page.onLoad.call(context, { from: "like" });
  assert.equal(context.data.profileMode, true);
  assert.equal(context.data.profileAfterLike, true);

  page.skipWeChatProfile.call(context);
  assert.deepEqual(backs, [true]);
  assert.deepEqual(relaunches, []);
});
