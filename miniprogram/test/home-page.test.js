require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

function loadHomePage(authExports = {}) {
  const authPath = require.resolve("../services/auth.ts");
  const homePath = require.resolve("../pages/home/index.ts");
  delete require.cache[authPath];
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

test("home redirects incomplete wechat accounts back to login", async () => {
  const launches = [];
  global.wx = {
    reLaunch(options) {
      launches.push(options.url);
    },
  };
  const { page } = loadHomePage({
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
  });
  const context = {
    data: { loading: false },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  await page.loadHome.call(context);

  assert.deepEqual(launches, ["/pages/login/index"]);
  assert.equal(context.data.loading, false);
  delete global.wx;
});
