const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const { test } = require("node:test");
const { AppiumClient, AppiumCommandError } = require("../support/appium-client");
const { parseJSONOutput, selectTunnel } = require("../support/ios-appium-runtime");
const { createIOSUIArtifacts } = require("../support/ios-ui-artifacts");
const {
  containsAll,
  iosSessionCapabilities,
  isPawtaleSource,
  predicateLiteral,
} = require("../support/ios-ui-smoke");

function tunnelRegistry(...udids) {
  return {
    status: "OK",
    tunnels: Object.fromEntries(udids.map((udid, index) => [udid, {
      connectionType: "USB",
      deviceId: index + 1,
      udid,
    }])),
  };
}

test("RemoteXPC tunnel selection discovers one USB iPhone and honors an explicit UDID", () => {
  assert.equal(selectTunnel(tunnelRegistry("ios-a")).udid, "ios-a");
  assert.equal(
    selectTunnel(tunnelRegistry("ios-a", "ios-b"), "ios-b").udid,
    "ios-b",
  );
  assert.throws(
    () => selectTunnel(tunnelRegistry()),
    /ios:tunnel/,
  );
  assert.throws(
    () => selectTunnel(tunnelRegistry("ios-a", "ios-b")),
    /多个 USB iPhone/,
  );
  assert.throws(
    () => selectTunnel(tunnelRegistry("ios-a"), "missing"),
    /未找到指定 iPhone/,
  );
});

test("iOS session capabilities keep WeChat data and use a bounded typing rate", () => {
  const capabilities = iosSessionCapabilities({
    iosTeamId: "TEAM123",
    wdaBundleId: "cn.example.wda",
    wechatBundleId: "com.tencent.xin",
  }, { udid: "ios-device" });
  assert.equal(capabilities.platformName, "iOS");
  assert.equal(capabilities["appium:udid"], "ios-device");
  assert.equal(capabilities["appium:noReset"], true);
  assert.equal(capabilities["appium:useNewWDA"], false);
  assert.equal(capabilities["appium:maxTypingFrequency"], 10);
  assert.equal(capabilities["appium:newCommandTimeout"], 600);
});

test("native source matching requires every visible marker", () => {
  assert.equal(containsAll("首页 分享给家人", ["首页", "分享给家人"]), true);
  assert.equal(containsAll("首页", ["首页", "分享给家人"]), false);
  assert.equal(predicateLiteral("a'b\\c"), "'a\\'b\\\\c'");
});

test("Pawtale source matching recognizes an authenticated non-home tab", () => {
  assert.equal(
    isPawtaleSource('<Button name="首页"/><Button name="记录"/>'
      + '<Button name="书稿"/><Button name="我的"/>'),
    true,
  );
  assert.equal(isPawtaleSource("微信 通讯录 发现 我"), false);
  assert.equal(isPawtaleSource("聊天文字提到首页记录书稿我的"), false);
});

test("Appium driver JSON parser ignores log lines before and after the object", () => {
  assert.deepEqual(
    parseJSONOutput('debug before\n{"xcuitest":{"version":"12.8.0"}}\ndebug after'),
    { xcuitest: { version: "12.8.0" } },
  );
});

test("Appium client reports protocol errors without echoing request bodies", async () => {
  const client = new AppiumClient("http://127.0.0.1:4723", {
    fetch: async () => ({
      ok: false,
      async json() {
        return { value: { error: "invalid argument", message: "bad command" } };
      },
    }),
  });
  await assert.rejects(
    client.request("/session", {
      method: "POST",
      body: { password: "must-not-appear" },
    }),
    (error) => {
      assert.equal(error instanceof AppiumCommandError, true);
      assert.equal(error.message, "bad command");
      assert.doesNotMatch(error.message, /must-not-appear/);
      return true;
    },
  );
});

test("Appium client timeout identifies the command that stalled", async () => {
  const client = new AppiumClient("http://127.0.0.1:4723", {
    fetch: async () => {
      const error = new Error("aborted");
      error.name = "TimeoutError";
      throw error;
    },
  });
  await assert.rejects(
    client.request("/session/example/source"),
    /GET \/session\/example\/source 在 30000ms 内未返回/,
  );
});

test("iOS failure artifacts skip screenshots when source cannot prove the page is safe", async () => {
  const runPath = await fs.mkdtemp(path.join(os.tmpdir(), "pawtale-ios-artifacts-"));
  let screenshotCalls = 0;
  try {
    const artifacts = await createIOSUIArtifacts({
      account: { email: "private@example.com", password: "private-password" },
    }, runPath);
    await artifacts.captureFailure({
      async source() {
        throw new Error("source timeout for private@example.com");
      },
      async screenshot() {
        screenshotCalls += 1;
      },
    }, new Error("failed with private-password"));
    assert.equal(screenshotCalls, 0);
    const failure = await fs.readFile(path.join(runPath, "failure.json"), "utf8");
    assert.doesNotMatch(failure, /private-password|private@example\.com/);
  } finally {
    await fs.rm(runPath, { recursive: true, force: true });
  }
});

test("iOS failure artifacts do not persist readable UI from an unverified WeChat page", async () => {
  const runPath = await fs.mkdtemp(path.join(os.tmpdir(), "pawtale-ios-artifacts-"));
  let screenshotCalls = 0;
  try {
    const artifacts = await createIOSUIArtifacts({
      account: { email: "private@example.com", password: "private-password" },
    }, runPath);
    await artifacts.captureFailure({
      async source() {
        return '<Application name="微信"><StaticText name="private chat"/></Application>';
      },
      async screenshot() {
        screenshotCalls += 1;
      },
    }, new Error("navigation failed"));
    assert.equal(screenshotCalls, 0);
    await assert.rejects(
      fs.access(path.join(runPath, "failure-source.xml")),
      { code: "ENOENT" },
    );
  } finally {
    await fs.rm(runPath, { recursive: true, force: true });
  }
});
