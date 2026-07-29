const assert = require("node:assert/strict");
const test = require("node:test");

function loadAuthorization() {
  delete require.cache[require.resolve("../services/recorder-authorization.ts")];
  return require("../services/recorder-authorization.ts");
}

test("recording requests privacy consent before microphone permission", async () => {
  const calls = [];
  global.wx = {
    getPrivacySetting({ success }) {
      calls.push("privacy-setting");
      success({ needAuthorization: true });
    },
    requirePrivacyAuthorize({ success }) {
      calls.push("privacy-consent");
      success();
    },
    authorize({ scope, success }) {
      calls.push(scope);
      success();
    },
  };

  await loadAuthorization().ensureRecorderAuthorization();

  assert.deepEqual(calls, [
    "privacy-setting",
    "privacy-consent",
    "scope.record",
  ]);
  delete global.wx;
});

test("recording explains how to recover denied microphone permission", async () => {
  global.wx = {
    getPrivacySetting({ success }) {
      success({ needAuthorization: false });
    },
    authorize({ fail }) {
      fail();
    },
  };

  await assert.rejects(
    loadAuthorization().ensureRecorderAuthorization(),
    /微信设置中允许使用麦克风/,
  );
  delete global.wx;
});
