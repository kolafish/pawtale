const assert = require("node:assert/strict");
const { after, before, test } = require("node:test");
const { connectIOSDevice } = require("../support/ios-device");
const {
  launchE2ESession,
  loginWithTestAccount,
  openProjects,
  shutdownE2ESession,
} = require("../support/session");

let session;
let systemInfo;
let failed = false;
let deviceCapabilities = {};

before(async () => {
  session = await launchE2ESession({ deferRuntimePreparation: true });
  try {
    systemInfo = await connectIOSDevice(session);
  } catch (error) {
    failed = true;
    await session.artifacts.captureFailure(session.miniProgram, error);
    throw error;
  }
});

after(async () => {
  if (!session) {
    return;
  }
  let teardownError = null;
  try {
    await shutdownE2ESession(session);
  } catch (error) {
    teardownError = error;
  }
  try {
    await session.artifacts.finish({
      deviceCapabilities,
      failed: failed || Boolean(teardownError),
      platform: systemInfo?.platform || "unknown",
      target: "ios-device",
      teardownError,
    });
  } catch (error) {
    teardownError = teardownError
      ? new AggregateError([teardownError, error], "真机清理与报告写入均失败")
      : error;
  }
  if (teardownError) {
    throw teardownError;
  }
});

test("iOS device logs in and renders the read-only memoir journey", async () => {
  try {
    assert.equal(String(systemInfo.platform).toLowerCase(), "ios");
    const homePage = await loginWithTestAccount(session);
    assert.equal(homePage.path, "pages/home/index");
    session.artifacts.mark("ios:login:complete");

    for (const tabPath of [
      "/pages/transcripts/index",
      "/pages/book/index",
      "/pages/me/index",
      "/pages/home/index",
    ]) {
      const page = await session.miniProgram.switchTab(tabPath);
      assert.equal(page.path, tabPath.slice(1));
      session.artifacts.mark(`ios:tab:${tabPath}:complete`);
    }

    const projectsPage = await openProjects(session);
    const projectState = await projectsPage.data();
    assert.ok(projectState.projects.length > 0, "iOS 真机应显示测试账号的基线回忆录");
    assert.ok(
      projectState.projects.some((project) => project.id === projectState.activeProjectId),
      "iOS 真机应显示当前活动回忆录",
    );
    session.artifacts.mark("ios:projects:loaded");

    const [setting, privacySetting, network] = await Promise.all([
      session.miniProgram.callWxMethod("getSetting"),
      session.miniProgram.callWxMethod("getPrivacySetting"),
      session.miniProgram.callWxMethod("getNetworkType"),
    ]);
    deviceCapabilities = {
      microphoneAuthorized: setting?.authSetting?.["scope.record"] === true,
      networkType: String(network?.networkType || "unknown"),
      privacyAuthorizationNeeded: privacySetting?.needAuthorization === true,
    };
    assert.notEqual(deviceCapabilities.networkType, "none", "iOS 真机需要可用网络");
    session.artifacts.mark("ios:capabilities:captured");
  } catch (error) {
    failed = true;
    await session.artifacts.captureFailure(session.miniProgram, error);
    throw error;
  }
});
