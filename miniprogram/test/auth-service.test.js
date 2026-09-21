require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

function authModule() {
  delete require.cache[require.resolve("../services/request.ts")];
  delete require.cache[require.resolve("../services/auth.ts")];
  return require("../services/auth.ts");
}

function installWxMock(handler) {
  const requests = [];
  const storage = new Map();
  global.wx = {
    getStorageSync(key) {
      if (key === "pawtale_backend_config") {
        return { httpBaseUrl: "https://api.example.com" };
      }
      return storage.get(key) || "";
    },
    getExtConfigSync() {
      return {};
    },
    setStorageSync(key, value) { storage.set(key, value); },
    removeStorageSync(key) { storage.delete(key); },
    login(options) {
      options.success({ code: "wx-code-1", errMsg: "login:ok" });
    },
    request(options) {
      requests.push(options);
      handler(options);
    },
  };
  return requests;
}

test("missing session never auto-registers a development account", async () => {
  const requests = installWxMock(() => {
    throw new Error("guest request must not reach backend");
  });
  const { requestWithBackendSession } = authModule();

  await assert.rejects(requestWithBackendSession("/api/mobile/memoir/me"), (error) => (
    error.statusCode === 401 && error.message === "请先登录"
  ));
  assert.equal(requests.length, 0);
  delete global.wx;
});

test("expired session is cleared without replaying the request", async () => {
  const requests = installWxMock((options) => {
    options.success({ statusCode: 401, data: { detail: "会话已过期" }, cookies: [], header: {} });
  });
  global.wx.setStorageSync("pawtale_backend_cookie", "pawtale_session=expired");
  const { requestWithBackendSession } = authModule();

  await assert.rejects(requestWithBackendSession("/api/mobile/memoir/me"), (error) => (
    error.statusCode === 401
  ));
  assert.equal(requests.length, 1);
  assert.equal(global.wx.getStorageSync("pawtale_backend_cookie"), "");
  delete global.wx;
});

test("logout keeps the cookie if server revocation cannot be confirmed", async () => {
  installWxMock((options) => options.fail({ errMsg: "request:fail network disconnected" }));
  global.wx.setStorageSync("pawtale_backend_cookie", "pawtale_session=active");
  const { logoutBackendSession } = authModule();

  await assert.rejects(logoutBackendSession());
  assert.equal(global.wx.getStorageSync("pawtale_backend_cookie"), "pawtale_session=active");
  delete global.wx;
});

test("account deletion requires a fresh WeChat code and clears the cookie only after success", async () => {
  const requests = installWxMock((options) => {
    assert.equal(options.url, "https://api.example.com/api/auth/me");
    assert.equal(options.method, "DELETE");
    assert.deepEqual(options.data, { code: "wx-code-1", miniprogram: "laoyu" });
    options.success({ statusCode: 204, data: "", cookies: [], header: {} });
  });
  global.wx.setStorageSync("pawtale_backend_cookie", "pawtale_session=active");
  const { deleteAccountWithWeChat } = authModule();

  await deleteAccountWithWeChat();
  assert.equal(requests.length, 1);
  assert.equal(global.wx.getStorageSync("pawtale_backend_cookie"), "");
  delete global.wx;
});

test("account deletion preserves the cookie when the server cannot confirm deletion", async () => {
  installWxMock((options) => options.fail({ errMsg: "request:fail network disconnected" }));
  global.wx.setStorageSync("pawtale_backend_cookie", "pawtale_session=active");
  const { deleteAccountWithWeChat } = authModule();

  await assert.rejects(deleteAccountWithWeChat());
  assert.equal(global.wx.getStorageSync("pawtale_backend_cookie"), "pawtale_session=active");
  delete global.wx;
});

test("loginWithWeChat exchanges wx login code through backend", async () => {
  const requests = installWxMock((options) => {
    assert.equal(options.url, "https://api.example.com/api/auth/wechat-login");
    assert.equal(options.method, "POST");
    assert.equal(options.timeout, 30000);
    assert.deepEqual(options.data, { code: "wx-code-1" });
    options.success({
      statusCode: 200,
      data: {
        user: {
          id: "user-1",
          email: "wechat_x@wechat.local",
          password_login_enabled: false,
          display_name: "微信用户",
          created_at: "2026-06-15T00:00:00",
        },
        needs_credentials: true,
      },
      cookies: ["pawtale_session=abc; Path=/"],
      header: {},
    });
  });
  const { loginWithWeChat } = authModule();

  const response = await loginWithWeChat();

  assert.equal(requests.length, 1);
  assert.equal(response.needs_credentials, true);
  assert.equal(response.user.password_login_enabled, false);
  assert.equal(global.wx.getStorageSync("pawtale_memoir_session").cache_identity.user_id, "user-1");
  delete global.wx;
});

test("loginWithWeChat does not depend on the retired getUserProfile result", async () => {
  const requests = installWxMock((options) => {
    assert.equal(options.url, "https://api.example.com/api/auth/wechat-login");
    assert.equal(options.method, "POST");
    assert.deepEqual(options.data, { code: "wx-code-1" });
    options.success({
      statusCode: 200,
      data: {
        user: {
          id: "user-1",
          email: "wechat_x@wechat.local",
          password_login_enabled: false,
          display_name: "微信用户",
          created_at: "2026-06-15T00:00:00",
        },
        needs_credentials: true,
      },
      cookies: ["pawtale_session=abc; Path=/"],
      header: {},
    });
  });
  global.wx.getUserProfile = () => {
    throw new Error("getUserProfile should not be called");
  };
  const { loginWithWeChat } = authModule();

  const response = await loginWithWeChat();

  assert.equal(requests.length, 1);
  assert.equal(response.user.display_name, "微信用户");
  assert.equal(response.user.avatar_url, undefined);
  delete global.wx;
});

test("updateBackendProfile patches current user display name", async () => {
  const requests = installWxMock((options) => {
    assert.equal(options.url, "https://api.example.com/api/auth/me");
    assert.equal(options.method, "PATCH");
    assert.deepEqual(options.data, {
      display_name: "新昵称",
    });
    options.success({
      statusCode: 200,
      data: {
        id: "user-1",
        email: "author@example.com",
        password_login_enabled: true,
        display_name: "新昵称",
        created_at: "2026-06-15T00:00:00",
      },
      cookies: [],
      header: {},
    });
  });
  const { updateBackendProfile } = authModule();

  const user = await updateBackendProfile({ display_name: "新昵称" });

  assert.equal(requests.length, 1);
  assert.equal(user.display_name, "新昵称");
  delete global.wx;
});

test("mutation timeout warns that the server result may be unknown", async () => {
  installWxMock((options) => {
    options.fail({ errMsg: "request:fail timeout" });
  });
  const { updateBackendProfile } = authModule();

  await assert.rejects(
    updateBackendProfile({ display_name: "新昵称" }),
    /结果可能仍在处理中，请刷新后确认，不要立即重复提交/,
  );
  delete global.wx;
});
