const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const { spawn } = require("node:child_process");
const { once } = require("node:events");
const { test } = require("node:test");
const { AppiumClient, AppiumCommandError } = require("../support/appium-client");
const {
  validateBackendAccount,
  validateIOSDeviceEnvironment,
} = require("../support/ios-device-preflight");
const { parseJSONOutput, selectTunnel } = require("../support/ios-appium-runtime");
const {
  authorizeSudo,
  ensureRemoteXPCTunnel,
  parseSupervisorPid,
  stopSupervisor,
  supervisorScript,
} = require("../support/ios-tunnel-manager");
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

test("one-command tunnel runner reuses an existing USB tunnel without sudo", async () => {
  let sudoCalls = 0;
  const handle = await ensureRemoteXPCTunnel({
    iosUdid: "",
    tunnelRegistryUrl: "http://127.0.0.1:43000",
  }, "/tmp/not-used", {
    fetch: async () => ({
      ok: true,
      async json() {
        return tunnelRegistry("ios-a");
      },
    }),
    runCommand: async () => {
      sudoCalls += 1;
      return { code: 0 };
    },
  });
  assert.equal(handle.owned, false);
  assert.equal(handle.tunnel.udid, "ios-a");
  assert.equal(sudoCalls, 0);
});

test("sudo authorization requires an interactive Terminal when no ticket is cached", async () => {
  await assert.rejects(
    authorizeSudo({
      stdinIsTTY: false,
      runCommand: async () => ({ code: 1 }),
    }),
    /交互式 Terminal/,
  );
  const calls = [];
  await authorizeSudo({
    stdinIsTTY: true,
    write: () => {},
    runCommand: async (command, args) => {
      calls.push([command, args]);
      return { code: calls.length === 1 ? 1 : 0 };
    },
  });
  assert.deepEqual(calls, [
    ["sudo", ["-n", "-v"]],
    ["sudo", ["-v"]],
  ]);
});

test("managed tunnel cleanup only signals its verified supervisor PID", async () => {
  const runPath = await fs.mkdtemp(path.join(os.tmpdir(), "pawtale-ios-tunnel-"));
  const pidPath = path.join(runPath, "tunnel-supervisor.pid");
  const calls = [];
  try {
    await fs.writeFile(pidPath, "4242\n");
    await stopSupervisor(pidPath, {
      processCommand: async () => (
        `/bin/sh ${supervisorScript()} 123 ${pidPath} node tunnel-creation.mjs `
        + "--tunnel-registry-port 43000"
      ),
      runCommand: async (command, args) => {
        calls.push([command, args]);
        return { code: 0 };
      },
      waitForProcessExit: async () => true,
    });
    assert.deepEqual(calls, [["sudo", ["-n", "kill", "-TERM", "4242"]]]);
    await assert.rejects(fs.access(pidPath), { code: "ENOENT" });
  } finally {
    await fs.rm(runPath, { recursive: true, force: true });
  }
  assert.equal(parseSupervisorPid("42\n"), 42);
  assert.throws(() => parseSupervisorPid("not-a-pid"), /有效 PID/);
});

test("tunnel supervisor stops its child when the owning runner disappears", async () => {
  const runPath = await fs.mkdtemp(path.join(os.tmpdir(), "pawtale-ios-watchdog-"));
  const pidPath = path.join(runPath, "supervisor.pid");
  const runner = spawn(process.execPath, ["-e", "setTimeout(() => {}, 150)"], {
    stdio: "ignore",
  });
  const supervisor = spawn(supervisorScript(), [
    String(runner.pid),
    pidPath,
    process.execPath,
    "-e",
    "setInterval(() => {}, 1000)",
  ], { stdio: "ignore" });
  let timeout;
  try {
    await Promise.race([
      once(supervisor, "exit"),
      new Promise((_, reject) => {
        timeout = setTimeout(
          () => reject(new Error("supervisor watchdog did not exit")),
          5000,
        );
      }),
    ]);
    assert.equal(supervisor.exitCode, 0);
    await assert.rejects(fs.access(pidPath), { code: "ENOENT" });
  } finally {
    clearTimeout(timeout);
    runner.kill("SIGKILL");
    supervisor.kill("SIGKILL");
    await fs.rm(runPath, { recursive: true, force: true });
  }
});

test("iOS preflight verifies Xcode, the tunnel driver, backend health, and account", async () => {
  const accessed = [];
  const requests = [];
  const config = {
    account: { email: "fixture@example.invalid", password: "private-password" },
    httpBaseUrl: "https://api.example.invalid",
    xcodeDeveloperDir: "/Applications/Xcode.app/Contents/Developer",
  };
  const fetchImpl = async (url, options = {}) => {
    requests.push({ body: options.body, method: options.method || "GET", url });
    return { ok: true, status: 200 };
  };
  const result = await validateIOSDeviceEnvironment(config, {
    access: async (filePath) => { accessed.push(filePath); },
    fetch: fetchImpl,
    tunnelScriptPath: "/isolated/appium/tunnel-creation.mjs",
  });
  assert.deepEqual(result, {
    accountVerified: true,
    backendHost: "api.example.invalid",
  });
  assert.deepEqual(accessed, [
    "/Applications/Xcode.app/Contents/Developer/usr/bin/xcodebuild",
    "/isolated/appium/tunnel-creation.mjs",
  ]);
  assert.deepEqual(requests.map(({ method, url }) => [method, url]), [
    ["GET", "https://api.example.invalid/health"],
    ["POST", "https://api.example.invalid/api/auth/login"],
  ]);
  assert.deepEqual(JSON.parse(requests[1].body), config.account);
});

test("backend preflight reports status without echoing account credentials", async () => {
  await assert.rejects(
    validateBackendAccount({
      account: { email: "private@example.invalid", password: "private-password" },
      httpBaseUrl: "https://api.example.invalid",
    }, async (url) => ({
      ok: !url.endsWith("/api/auth/login"),
      status: 401,
    })),
    (error) => {
      assert.match(error.message, /HTTP 401/);
      assert.doesNotMatch(error.message, /private@example|private-password/);
      return true;
    },
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
