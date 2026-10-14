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
  let authorized;
  let authorizeCalls = 0;
  global.wx = {
    getPrivacySetting(options) {
      options.success({ needAuthorization: false });
    },
    getSetting(options) {
      options.success({ authSetting: { "scope.record": authorized } });
    },
    authorize(options) {
      authorizeCalls += 1;
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
  assert.equal(authorizeCalls, 1, "首次拒绝返回可恢复的权限错误");
  authorized = false;
  await assert.rejects(ensureRecorderPrivacyAuthorization(), isRecorderPermissionDeniedError);
  assert.equal(authorizeCalls, 1, "已拒绝时不重复请求授权");
  authorized = true;
  await ensureRecorderPrivacyAuthorization();
  assert.equal(authorizeCalls, 1, "已允许时直接继续");
  delete global.wx;
});

test("wechat recorder settings reports whether microphone access is enabled", async () => {
  let authorized = true;
  global.wx = {
    openSetting(options) {
      options.success({ authSetting: { "scope.record": authorized } });
    },
  };
  const { openRecorderPermissionSettings } = privacyModule();

  assert.equal(await openRecorderPermissionSettings(), true);
  authorized = false;
  assert.equal(await openRecorderPermissionSettings(), false);
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

test("reading the privacy contract opens WeChat's configured document without authorizing", async () => {
  let opened = 0;
  global.wx = {
    openPrivacyContract(options) { opened += 1; options.success({}); },
    requirePrivacyAuthorize() { throw new Error("reading is not consent"); },
    authorize() { throw new Error("reading does not request the microphone"); },
  };
  await privacyModule().openWechatPrivacyContract();
  assert.equal(opened, 1);
  delete global.wx;
});

test("unavailable or failed privacy document opening gives a visible retryable error", async () => {
  global.wx = {};
  await assert.rejects(privacyModule().openWechatPrivacyContract(), /更新微信/);
  global.wx = { openPrivacyContract(options) { options.fail({ errMsg: "openPrivacyContract:fail" }); } };
  await assert.rejects(privacyModule().openWechatPrivacyContract(), /稍后再试/);
  delete global.wx;
});
