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
  global.wx = {
    getStorageSync(key) {
      if (key === "pawtale_backend_config") {
        return { httpBaseUrl: "https://api.example.com" };
      }
      return "";
    },
    getExtConfigSync() {
      return {};
    },
    setStorageSync() {},
    removeStorageSync() {},
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

test("loginWithWeChat exchanges wx login code through backend", async () => {
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
  const { loginWithWeChat } = authModule();

  const response = await loginWithWeChat();

  assert.equal(requests.length, 1);
  assert.equal(response.needs_credentials, true);
  assert.equal(response.user.password_login_enabled, false);
  delete global.wx;
});

test("setAccountCredentials saves email password for current backend session", async () => {
  const requests = installWxMock((options) => {
    assert.equal(options.url, "https://api.example.com/api/auth/set-credentials");
    assert.equal(options.method, "POST");
    assert.deepEqual(options.data, {
      email: "author@example.com",
      password: "password123",
      display_name: "余主任",
    });
    options.success({
      statusCode: 200,
      data: {
        user: {
          id: "user-1",
          email: "author@example.com",
          password_login_enabled: true,
          display_name: "余主任",
          created_at: "2026-06-15T00:00:00",
        },
        needs_credentials: false,
      },
      cookies: [],
      header: {},
    });
  });
  const { setAccountCredentials } = authModule();

  const response = await setAccountCredentials({
    email: "author@example.com",
    password: "password123",
    display_name: "余主任",
  });

  assert.equal(requests.length, 1);
  assert.equal(response.user.email, "author@example.com");
  assert.equal(response.needs_credentials, false);
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
