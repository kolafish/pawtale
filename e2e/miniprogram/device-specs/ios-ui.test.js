const assert = require("node:assert/strict");
const path = require("node:path");
const { after, before, test } = require("node:test");
const { AppiumClient } = require("../support/appium-client");
const { E2E_ROOT, readIOSNativeConfig } = require("../support/config");
const { readTunnel } = require("../support/ios-appium-runtime");
const { createIOSUIArtifacts } = require("../support/ios-ui-artifacts");
const { createIOSProjectFixture } = require("../support/ios-project-fixture");
const {
  runIOSAccountProjectJourney,
} = require("../support/ios-ui-account-projects");
const {
  iosSessionCapabilities,
  runIOSUISmoke,
} = require("../support/ios-ui-smoke");

let artifacts;
let client;
let config;
let failed = false;
let fixture;
let result = null;
let tunnel;

before(async () => {
  config = readIOSNativeConfig();
  fixture = await createIOSProjectFixture(config);
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
  const teardownErrors = [];
  if (fixture) {
    try {
      await fixture.cleanup();
      artifacts?.mark("ios-ui:project-fixture:cleaned");
    } catch (error) {
      teardownErrors.push(error);
    }
  }
  if (client) {
    try {
      await client.deleteSession();
    } catch (error) {
      teardownErrors.push(error);
    }
  }
  const teardownError = teardownErrors.length > 1
    ? new AggregateError(teardownErrors, "真机项目夹具与 Appium 会话清理失败")
    : teardownErrors[0] || null;
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

test("iOS real device verifies account authentication and the memoir project lifecycle", async () => {
  try {
    const smoke = await runIOSUISmoke(client, config, artifacts);
    const projects = await runIOSAccountProjectJourney(
      client,
      config,
      artifacts,
      fixture,
      await client.source(),
    );
    result = { ...smoke, ...projects };
    assert.equal(result.accountVerified, true);
    assert.equal(result.accountReauthenticated, true);
    assert.equal(result.baselineRestored, true);
    assert.deepEqual(result.pages, ["首页", "设置"]);
    assert.deepEqual(
      result.projectJourney,
      ["cancel", "create", "rename", "switch", "delete"],
    );
  } catch (error) {
    failed = true;
    await artifacts.captureFailure(client, error);
    throw error;
  }
});
