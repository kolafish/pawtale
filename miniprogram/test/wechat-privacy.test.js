require("./register-ts");

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

function privacyModule() {
  delete require.cache[require.resolve("../services/wechat-privacy.ts")];
  return require("../services/wechat-privacy.ts");
}

test("app config leaves recorder permission to the runtime scope request", () => {
  const appConfig = JSON.parse(fs.readFileSync(path.join(__dirname, "../app.json"), "utf8"));
  assert.equal(appConfig.permission?.["scope.record"], undefined);
});

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

test("wechat recorder authorization requests the mini program microphone scope", async () => {
  let recorderAuthorizeCalls = 0;
  global.wx = {
    getPrivacySetting(options) {
      options.success({ needAuthorization: false });
    },
    getSetting(options) {
      options.success({ authSetting: {} });
    },
    authorize(options) {
      recorderAuthorizeCalls += 1;
      assert.equal(options.scope, "scope.record");
      options.success({ errMsg: "authorize:ok" });
    },
  };
  const { ensureRecorderPrivacyAuthorization } = privacyModule();

  await ensureRecorderPrivacyAuthorization();

  assert.equal(recorderAuthorizeCalls, 1);
  delete global.wx;
});

test("wechat recorder authorization sends denied users to settings", async () => {
  global.wx = {
    getPrivacySetting(options) {
      options.success({ needAuthorization: false });
    },
    getSetting(options) {
      options.success({ authSetting: { "scope.record": false } });
    },
    authorize(options) {
      options.fail({ errMsg: "authorize:fail auth deny" });
    },
  };
  const {
    ensureRecorderPrivacyAuthorization,
    isRecorderPermissionDeniedError,
  } = privacyModule();

  await assert.rejects(
    ensureRecorderPrivacyAuthorization(),
    (error) => isRecorderPermissionDeniedError(error) && /小程序设置/.test(error.message),
  );
  delete global.wx;
});

test("wechat recorder settings reports whether microphone access is enabled", async () => {
  global.wx = {
    openSetting(options) {
      options.success({ authSetting: { "scope.record": true } });
    },
  };
  const { openRecorderPermissionSettings } = privacyModule();

  assert.equal(await openRecorderPermissionSettings(), true);
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

test("wechat recorder errors hide native sync channel details", () => {
  const { wechatRecorderErrorMessage } = privacyModule();

  assert.match(
    wechatRecorderErrorMessage("received error code 1 on sync-0"),
    /麦克风启动失败/,
  );
  assert.match(
    wechatRecorderErrorMessage("operateRecorder:fail auth deny"),
    /没有麦克风权限/,
  );
  assert.match(
    wechatRecorderErrorMessage("operateRecorder:fail is recording or paused"),
    /麦克风正在使用中/,
  );
});
