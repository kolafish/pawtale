const assert = require("node:assert/strict");
const path = require("node:path");
const { after, before, test } = require("node:test");
const { AppiumClient } = require("../support/appium-client");
const { E2E_ROOT, readIOSNativeConfig } = require("../support/config");
const { readTunnel } = require("../support/ios-appium-runtime");
const { createIOSUIArtifacts } = require("../support/ios-ui-artifacts");
const {
  iosSessionCapabilities,
  runIOSUISmoke,
} = require("../support/ios-ui-smoke");

let artifacts;
let client;
let config;
let failed = false;
let result = null;
let tunnel;

before(async () => {
  config = readIOSNativeConfig();
  tunnel = await readTunnel(config);
  const runPath = process.env.PAWTALE_E2E_IOS_RUN_PATH
    || path.join(E2E_ROOT, "artifacts", "ios-ui-direct");
  artifacts = await createIOSUIArtifacts(config, runPath);
  client = new AppiumClient(config.appiumServerUrl);
  try {
    await client.createSession(iosSessionCapabilities(config, tunnel));
  } catch (error) {
    if (String(error.message).includes("could not be, unlocked")) {
      throw new Error("iPhone 已锁定；请解锁手机并保持屏幕亮着，再运行 iOS 真机测试");
    }
    throw error;
  }
  artifacts.mark("ios-ui:session:created");
});

after(async () => {
  let teardownError = null;
  if (client) {
    try {
      await client.deleteSession();
    } catch (error) {
      teardownError = error;
    }
  }
  if (artifacts) {
    await artifacts.finish({
      device: tunnel ? { connectionType: tunnel.connectionType, udid: tunnel.udid } : null,
      failed: failed || Boolean(teardownError),
      result,
      target: "ios-ui",
      teardownError,
    });
  }
  if (teardownError) {
    throw teardownError;
  }
});

test("iOS real device controls WeChat and verifies the authenticated memoir journey", async () => {
  try {
    result = await runIOSUISmoke(client, config, artifacts);
    assert.equal(result.accountVerified, true);
    assert.deepEqual(result.tabs, ["首页", "记录", "书稿", "我的"]);
  } catch (error) {
    failed = true;
    await artifacts.captureFailure(client, error);
    throw error;
  }
});
