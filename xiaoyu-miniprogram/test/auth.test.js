require("../../miniprogram/test/register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

function loadAuthModule() {
  delete require.cache[require.resolve("../services/request.ts")];
  delete require.cache[require.resolve("../services/auth.ts")];
  return require("../services/auth.ts");
}

test("Xiaoyu WeChat login sends its miniprogram identity and stores the session", async () => {
  const storage = new Map();
  const requests = [];
  global.wx = {
    getStorageSync(key) {
      return storage.get(key) || "";
    },
    setStorageSync(key, value) {
      storage.set(key, value);
    },
    removeStorageSync(key) {
      storage.delete(key);
    },
    login(options) {
      options.success({ code: "xiaoyu-code" });
    },
    request(options) {
      requests.push(options);
      options.success({
        statusCode: 200,
        data: {
          user: { id: "user-1", display_name: "微信用户" },
          needs_credentials: false,
        },
        cookies: ["pawtale_session=xiaoyu-session; Path=/; HttpOnly"],
        header: {},
      });
    },
  };

  const response = await loadAuthModule().loginWithWeChat();

  assert.equal(response.user.id, "user-1");
  assert.equal(requests[0].url, "https://pawtale-api.spaceflows.cn/api/auth/wechat-login");
  assert.equal(requests[0].method, "POST");
  assert.deepEqual(requests[0].data, {
    code: "xiaoyu-code",
    miniprogram: "xiaoyu",
  });
  assert.equal(storage.get("xiaoyu_backend_cookie"), "pawtale_session=xiaoyu-session");
  delete global.wx;
});
