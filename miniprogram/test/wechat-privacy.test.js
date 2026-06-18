require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

function privacyModule() {
  delete require.cache[require.resolve("../services/wechat-privacy.ts")];
  return require("../services/wechat-privacy.ts");
}

test("wechat privacy authorization passes when no privacy prompt is needed", async () => {
  let authorizeCalls = 0;
  global.wx = {
    getPrivacySetting(options) {
      options.success({ needAuthorization: false });
    },
    requirePrivacyAuthorize() {
      authorizeCalls += 1;
    },
  };
  const { ensureRecorderPrivacyAuthorization } = privacyModule();

  await ensureRecorderPrivacyAuthorization();

  assert.equal(authorizeCalls, 0);
  delete global.wx;
});

test("wechat privacy authorization requests consent before recorder usage", async () => {
  let authorizeCalls = 0;
  global.wx = {
    getPrivacySetting(options) {
      options.success({ needAuthorization: true });
    },
    requirePrivacyAuthorize(options) {
      authorizeCalls += 1;
      options.success({ errMsg: "requirePrivacyAuthorize:ok" });
    },
  };
  const { ensureRecorderPrivacyAuthorization } = privacyModule();

  await ensureRecorderPrivacyAuthorization();

  assert.equal(authorizeCalls, 1);
  delete global.wx;
});

test("wechat privacy errors explain missing mini program privacy declaration", async () => {
  global.wx = {
    getPrivacySetting(options) {
      options.fail({ errMsg: "operateRecorder:fail appid privacy api banned" });
    },
  };
  const { ensureRecorderPrivacyAuthorization, wechatPrivacyErrorMessage } = privacyModule();

  await assert.rejects(
    ensureRecorderPrivacyAuthorization(),
    /用户隐私保护指引.*麦克风\/录音用途声明/,
  );
  assert.match(
    wechatPrivacyErrorMessage("operateRecorder:fail appid privacy api banned"),
    /用户隐私保护指引.*麦克风\/录音用途声明/,
  );
  delete global.wx;
});
