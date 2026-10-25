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

function loadLoginPage({ authExports = {}, profileExports = {} } = {}) {
  mockModule("../services/auth.ts", {
    loginWithWeChat: unmockedAsync("loginWithWeChat"),
    updateBackendProfile: unmockedAsync("updateBackendProfile"),
    ...authExports,
  });
  mockModule("../services/user-profile.ts", {
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

test("login consent checkbox is bound independently from opening the privacy document", () => {
  const template = fs.readFileSync(path.join(__dirname, "../pages/login/index.wxml"), "utf8");
  assert.match(template, /<checkbox-group\b[^>]*bindchange="changePrivacyConsent"/);
  assert.match(template, /<checkbox\b[^>]*value="privacy_contract"[^>]*checked="\{\{privacyAgreed\}\}"/);
  assert.match(template, /<\/label>\s*<view\b[^>]*catchtap="openPrivacyContract"/);
});

function installPrivacyStorage() {
  const storage = new Map();
  global.wx = {
    getStorageSync: (key) => storage.get(key),
    setStorageSync: (key, value) => storage.set(key, value),
    removeStorageSync: (key) => storage.delete(key),
    reLaunch() {},
  };
  return storage;
}

test("first login requires consent and later login pages remember the explicit choice", async () => {
  let loginCalls = 0;
  installPrivacyStorage();
  const { page } = loadLoginPage({
    authExports: { loginWithWeChat: async () => { loginCalls += 1; return { user: {} }; } },
  });
  const context = pageContext(page);
  context.onLoad();

  assert.equal(context.data.privacyAgreed, false);
  await context.submitWeChat();

  assert.equal(loginCalls, 0);
  assert.equal(context.data.wechatLoading, false);
  assert.match(context.data.error, /请先阅读并勾选同意/);

  context.changePrivacyConsent({ detail: { value: ["privacy_contract"] } });
  assert.equal(loginCalls, 0);
  await context.submitWeChat();
  assert.equal(loginCalls, 1);

  const next = pageContext(page);
  next.onLoad();
  assert.equal(next.data.privacyAgreed, true);
  await next.submitWeChat();
  assert.equal(loginCalls, 2);
});

test("unchecking consent blocks login again including on a new login page", async () => {
  let loginCalls = 0;
  installPrivacyStorage();
  const { page } = loadLoginPage({
    authExports: { loginWithWeChat: async () => { loginCalls += 1; } },
  });
  const context = pageContext(page);

  context.changePrivacyConsent({ detail: { value: ["privacy_contract"] } });
  assert.equal(context.data.privacyAgreed, true);
  assert.equal(loginCalls, 0);
  context.changePrivacyConsent({ detail: { value: [] } });
  await context.submitWeChat();

  assert.equal(context.data.privacyAgreed, false);
  assert.equal(loginCalls, 0);
  const next = pageContext(page);
  next.onLoad();
  assert.equal(next.data.privacyAgreed, false);
  await next.submitWeChat();
  assert.equal(loginCalls, 0);
});

test("a changed contract, cleared storage or an unknown old value requires consent again", async () => {
  const storage = installPrivacyStorage();
  let loginCalls = 0;
  const { page } = loadLoginPage({
    authExports: { loginWithWeChat: async () => { loginCalls += 1; } },
  });
  const context = pageContext(page);
  context.changePrivacyConsent({ detail: { value: ["privacy_contract"] } });
  const [key] = storage.keys();
  for (const oldValue of ["previous-contract", true, { agreed: true }, undefined]) {
    storage.set(key, oldValue);
    const next = pageContext(page);
    next.onLoad();
    assert.equal(next.data.privacyAgreed, false);
    await next.submitWeChat();
  }
  assert.equal(loginCalls, 0);
});

test("failed consent storage stays visible and does not grant consent on the next page", () => {
  installPrivacyStorage();
  global.wx.setStorageSync = () => { throw new Error("storage unavailable"); };
  const { page } = loadLoginPage();
  const context = pageContext(page);
  context.changePrivacyConsent({ detail: { value: ["privacy_contract"] } });
  assert.equal(context.data.privacyAgreed, true);
  assert.match(context.data.error, /无法保存/);
  const next = pageContext(page);
  next.onLoad();
  assert.equal(next.data.privacyAgreed, false);
  global.wx.getStorageSync = () => { throw new Error("storage unavailable"); };
  next.onLoad();
  assert.equal(next.data.privacyAgreed, false);
});

test("a new wechat user goes straight home without the avatar and nickname step", async () => {
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

  context.changePrivacyConsent({ detail: { value: ["privacy_contract"] } });
  await page.submitWeChat.call(context);

  assert.deepEqual(relaunches, ["/pages/home/index"]);
  assert.equal(context.data.profileMode, false);
  assert.equal(context.data.wechatLoading, false);
});

test("after a first like the chosen nickname and avatar are saved before returning", async () => {
  const profileUpdates = [];
  const avatarUploads = [];
  const backs = [];
  global.wx = {
    navigateBack: () => backs.push(true),
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
  assert.deepEqual(backs, [true]);
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

  page.skipWeChatProfile.call(context);
  assert.deepEqual(backs, [true]);
  assert.deepEqual(relaunches, []);
});

test("privacy entry shows an immediate opening error and permits a successful retry", async () => {
  const toasts = [];
  global.wx = { getStorageSync: () => "", showToast: (options) => toasts.push(options), openPrivacyContract(options) { options.fail({}); } };
  const { page } = loadLoginPage();
  const context = pageContext(page);
  await context.openPrivacyContract();
  assert.match(toasts[0].title, /隐私保护指引/);
  assert.equal(context.data.privacyAgreed, false);
  global.wx.openPrivacyContract = (options) => options.success({});
  await context.openPrivacyContract();
  assert.equal(toasts.length, 1);
  assert.equal(context.data.privacyAgreed, false);
  delete global.wx;
});
