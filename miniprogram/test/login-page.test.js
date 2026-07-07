require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");
const { loadPageModule, mockModule, pageContext } = require("./page-harness");

function loadLoginPage({ authExports = {} } = {}) {
  mockModule("../services/auth.ts", {
    isValidEmail: (email) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim()),
    loginWithEmail: async () => {
      throw new Error("loginWithEmail was not mocked");
    },
    loginWithWeChat: async () => {
      throw new Error("loginWithWeChat was not mocked");
    },
    ...authExports,
  });

  return loadPageModule("../pages/login/index.ts");
}

test("login page relaunches home after first wechat registration", async () => {
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
  delete global.wx;
});

test("login page validates email before Pawtale account login", async () => {
  let loginCalls = 0;
  const { page } = loadLoginPage({
    authExports: {
      loginWithEmail: async () => {
        loginCalls += 1;
        return {};
      },
    },
  });
  const context = pageContext(page, {
    email: "author",
    password: "password123",
  });

  await page.submitAccount.call(context);

  assert.equal(loginCalls, 0);
  assert.match(context.data.error, /邮箱/);
});

test("login page logs in with trimmed Pawtale account email", async () => {
  const loginCalls = [];
  const relaunches = [];
  global.wx = {
    reLaunch(options) {
      relaunches.push(options.url);
    },
  };
  const { page } = loadLoginPage({
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
  const context = pageContext(page, {
    email: " author@example.com ",
    password: "password123",
  });

  await page.submitAccount.call(context);

  assert.deepEqual(loginCalls, [
    {
      email: "author@example.com",
      password: "password123",
    },
  ]);
  assert.deepEqual(relaunches, ["/pages/home/index"]);
  delete global.wx;
});
