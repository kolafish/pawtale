require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

function loadLoginPage({ authExports = {} } = {}) {
  const authPath = require.resolve("../services/auth.ts");
  const loginPath = require.resolve("../pages/login/index.ts");
  delete require.cache[authPath];
  delete require.cache[loginPath];
  require.cache[authPath] = {
    id: authPath,
    filename: authPath,
    loaded: true,
    exports: {
      loginWithEmail: async () => {
        throw new Error("loginWithEmail was not mocked");
      },
      loginWithWeChat: async () => {
        throw new Error("loginWithWeChat was not mocked");
      },
      setAccountCredentials: async () => {
        throw new Error("setAccountCredentials was not mocked");
      },
      ...authExports,
    },
  };

  let page = null;
  const previousPage = global.Page;
  global.Page = (definition) => {
    page = definition;
  };
  require(loginPath);
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

test("login page requires nickname before saving first credentials", async () => {
  let credentialsCalls = 0;
  const { page } = loadLoginPage({
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
    credentialEmail: "author@example.com",
    credentialPassword: "password123",
    credentialDisplayName: "",
  });

  await page.submitCredentials.call(context);

  assert.equal(credentialsCalls, 0);
  assert.match(context.data.error, /昵称/);
});

test("login page saves user-entered nickname with first credentials", async () => {
  const credentialsCalls = [];
  const relaunches = [];
  global.wx = {
    reLaunch(options) {
      relaunches.push(options.url);
    },
  };
  const { page } = loadLoginPage({
    authExports: {
      setAccountCredentials: async (input) => {
        credentialsCalls.push(input);
        return {
          user: {
            id: "user-1",
            email: input.email,
            password_login_enabled: true,
            display_name: input.display_name,
            created_at: "2026-06-15T00:00:00",
          },
          needs_credentials: false,
        };
      },
    },
  });
  const context = pageContext(page, {
    credentialEmail: " author@example.com ",
    credentialPassword: "password123",
    credentialDisplayName: " 微信昵称 ",
  });

  await page.submitCredentials.call(context);

  assert.deepEqual(credentialsCalls, [
    {
      email: "author@example.com",
      password: "password123",
      display_name: "微信昵称",
    },
  ]);
  assert.deepEqual(relaunches, ["/pages/home/index"]);
  delete global.wx;
});
