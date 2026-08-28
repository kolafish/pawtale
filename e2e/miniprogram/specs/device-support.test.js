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
const {
  descendantProcessGroups,
  parseJSONOutput,
  selectTunnel,
  stopOwnedProcessGroup,
} = require("../support/ios-appium-runtime");
const {
  authorizeSudo,
  ensureRemoteXPCTunnel,
  parseSupervisorPid,
  stopSupervisor,
  supervisorScript,
} = require("../support/ios-tunnel-manager");
const {
  createIOSUIArtifacts,
  isSafePawtaleArtifactSource,
} = require("../support/ios-ui-artifacts");
const {
  projectCardActionPoint,
  tapProjectCardAction,
  tapScrollableNamedElement,
} = require("../support/ios-ui-account-projects");
const {
  containsAll,
  focusTextField,
  hideIOSKeyboard,
  iosSessionCapabilities,
  isDeleteConfirmationSource,
  isPawtaleSource,
  loginSourceState,
  maskedValuePresent,
  predicateLiteral,
  replaceTextField,
  returnToPreviousPage,
  submitAccountLogin,
  tapLargestNamedElementAndWait,
  waitForMaskedTextField,
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

test("Appium cleanup terminates the exact owned process group before escalating", async () => {
  let alive = true;
  const signals = [];
  await stopOwnedProcessGroup(4242, {
    kill(pid, signal) {
      assert.equal(pid, -4242);
      if (signal === 0) {
        if (!alive) {
          const error = new Error("not found");
          error.code = "ESRCH";
          throw error;
        }
        return;
      }
      signals.push(signal);
      alive = false;
    },
    sleep: async () => {},
  });
  assert.deepEqual(signals, ["SIGTERM"]);
});

test("Appium cleanup stops waiting when only protected system group members remain", async () => {
  const signals = [];
  let checks = 0;
  await stopOwnedProcessGroup(4242, {
    kill(pid, signal) {
      assert.equal(pid, -4242);
      if (signal === 0) {
        checks += 1;
        if (checks > 1) {
          const error = new Error("not permitted");
          error.code = "EPERM";
          throw error;
        }
        return;
      }
      signals.push(signal);
    },
    sleep: async () => {},
  });
  assert.deepEqual(signals, ["SIGTERM"]);
});

test("Appium cleanup discovers child-owned process groups before the parent exits", () => {
  assert.deepEqual(descendantProcessGroups(100, [
    { pid: 100, ppid: 1, processGroupId: 100, command: "appium" },
    { pid: 101, ppid: 100, processGroupId: 100, command: "node helper" },
    { pid: 102, ppid: 101, processGroupId: 102, command: "xcodebuild" },
    { pid: 103, ppid: 102, processGroupId: 102, command: "build service" },
    { pid: 200, ppid: 1, processGroupId: 200, command: "unrelated" },
  ]), [102]);
});

test("native source matching requires every visible marker", () => {
  assert.equal(containsAll("首页 分享给家人", ["首页", "分享给家人"]), true);
  assert.equal(containsAll("首页", ["首页", "分享给家人"]), false);
  assert.equal(predicateLiteral("a'b\\c"), "'a\\'b\\\\c'");
  assert.equal(
    isDeleteConfirmationSource(
      '<Text name="删除回忆录"/><Text name="取消"/><Text name="删除"/>',
    ),
    true,
  );
  assert.equal(
    isDeleteConfirmationSource('<Button name="删除回忆录"/><Button name="取消"/>'),
    false,
  );
});

test("secure iOS fields accept a fixed mask without inferring password length", () => {
  assert.equal(maskedValuePresent("********", "至少 8 位"), true);
  assert.equal(maskedValuePresent("至少 8 位", "至少 8 位"), false);
  assert.equal(maskedValuePresent("", "至少 8 位"), false);
});

test("native text entry targets the requested field instead of the current keyboard focus", async () => {
  const fields = {
    "name@example.com": { id: "email", placeholder: "name@example.com", value: "" },
    "至少 8 位": { id: "password", placeholder: "至少 8 位", value: "" },
  };
  let active = fields["name@example.com"];
  const client = {
    async findElements(_using, predicate) {
      const field = Object.values(fields).find(({ placeholder }) => (
        predicate.includes(`placeholderValue == '${placeholder}'`)
      ));
      return field ? [field] : [];
    },
    async clickElement(field) {
      active = field;
    },
    async activeElement() {
      return active;
    },
    elementId(field) {
      return field.id;
    },
    async elementAttribute(field, name) {
      return name === "placeholderValue" ? field.placeholder : field.value;
    },
    async clearElement(field) {
      field.value = "";
    },
    async typeElement(field, text) {
      field.value = field.placeholder === "至少 8 位" ? "********" : text;
    },
    async typeKeys() {
      throw new Error("global keyboard input must not be used");
    },
  };

  await replaceTextField(client, "name@example.com", "fixture@example.invalid");
  await replaceTextField(client, "至少 8 位", "private-password", { masked: true });

  assert.equal(fields["name@example.com"].value, "fixture@example.invalid");
  assert.equal(fields["至少 8 位"].value, "********");
});

test("native text entry refuses to type when the requested field did not receive focus", async () => {
  const requested = { id: "password", placeholder: "至少 8 位" };
  const active = { id: "email", placeholder: "name@example.com" };
  let typed = false;
  const client = {
    async clickElement() {},
    async activeElement() {
      return active;
    },
    elementId(field) {
      return field.id;
    },
    async elementAttribute(field) {
      return field.placeholder;
    },
    async typeElement() {
      typed = true;
    },
  };

  await assert.rejects(
    focusTextField(client, requested, "至少 8 位", {
      focusTimeoutMs: 10,
      focusIntervalMs: 1,
      pointerFallback: false,
    }),
    /输入框焦点校验失败/,
  );
  assert.equal(typed, false);
});

test("native text entry accepts the exact active element when its placeholder disappears", async () => {
  const requested = { id: "password", placeholder: "至少 8 位" };
  const active = { id: "password", placeholder: "" };
  let placeholderReads = 0;
  const client = {
    async clickElement() {},
    async activeElement() {
      return active;
    },
    elementId(field) {
      return field.id;
    },
    async elementAttribute() {
      placeholderReads += 1;
      return "";
    },
  };

  await focusTextField(client, requested, "至少 8 位");
  assert.equal(placeholderReads, 0);
});

test("native text entry waits through a transient missing active element", async () => {
  const requested = { id: "password", placeholder: "至少 8 位" };
  let reads = 0;
  const client = {
    async clickElement() {},
    async activeElement() {
      reads += 1;
      if (reads === 1) {
        const error = new Error("no active element yet");
        error.appiumError = "no such element";
        throw error;
      }
      return requested;
    },
    elementId(field) {
      return field.id;
    },
  };

  await focusTextField(client, requested, "至少 8 位", {
    focusTimeoutMs: 100,
    focusIntervalMs: 1,
  });
  assert.equal(reads, 2);
});

test("native text entry refinds and pointer-taps the exact field after element focus fails", async () => {
  const stale = { id: "password-stale", placeholder: "至少 8 位" };
  const fresh = { id: "password-fresh", placeholder: "至少 8 位" };
  let active = { id: "email", placeholder: "name@example.com" };
  let pointerTapped = null;
  const client = {
    async clickElement() {},
    async activeElement() {
      return active;
    },
    elementId(field) {
      return field.id;
    },
    async elementAttribute(field) {
      return field.placeholder;
    },
    async findElements() {
      return [fresh];
    },
    async tapElementCenter(field) {
      pointerTapped = field;
      active = field;
    },
  };

  const focused = await focusTextField(client, stale, "至少 8 位", {
    focusTimeoutMs: 5,
    focusIntervalMs: 1,
  });

  assert.equal(pointerTapped, fresh);
  assert.equal(focused, fresh);
});

test("masked text entry waits for Face ID and retries only after the password field is active", async () => {
  const password = { id: "password", placeholder: "至少 8 位", value: "" };
  const faceID = { id: "face-id", placeholder: "" };
  let active = faceID;
  let typed = 0;
  let announced = 0;
  const client = {
    async findElements() {
      return [password];
    },
    async elementAttribute(field, name) {
      return name === "placeholderValue" ? field.placeholder : field.value;
    },
    async activeElement() {
      if (active === faceID) {
        active = password;
        return faceID;
      }
      return active;
    },
    elementId(field) {
      return field.id;
    },
    async typeElement(field) {
      typed += 1;
      field.value = "********";
    },
  };

  await waitForMaskedTextField(client, "至少 8 位", "private-password", {
    biometricTimeoutMs: 1000,
    biometricIntervalMs: 1,
    onBiometricWait() {
      announced += 1;
    },
  });

  assert.equal(announced, 1);
  assert.equal(typed, 1);
  assert.equal(password.value, "********");
});

test("native login hides the iOS keyboard through Appium state instead of page XML", async () => {
  let shown = true;
  const active = { id: "email" };
  const calls = [];
  const client = {
    async isKeyboardShown() {
      return shown;
    },
    async activeElement() {
      return active;
    },
    async mobileKeys(element, keys) {
      calls.push([element.id, keys]);
      shown = false;
    },
  };

  await hideIOSKeyboard(client, "测试键盘收起");
  assert.deepEqual(calls, [["email", ["\r"]]]);
  assert.equal(shown, false);
});

test("native login treats loading as proof that the semantic element click submitted", async () => {
  const button = { id: "login" };
  const marks = [];
  let sourceReads = 0;
  let elementClicks = 0;
  const client = {
    async findElements() {
      return [button];
    },
    async elementRect() {
      return { x: 20, y: 600, width: 390, height: 52 };
    },
    async clickElement() {
      elementClicks += 1;
    },
    async source() {
      sourceReads += 1;
      return sourceReads === 1
        ? '<Button name="登录中"/><Text name="欢迎登录"/>'
        : '<Button name="首页"/><Button name="记录"/>';
    },
    async tapElementCenter() {
      throw new Error("acknowledged element click must not use pointer fallback");
    },
  };
  const artifacts = { mark(value) { marks.push(value); } };

  const source = await submitAccountLogin(client, artifacts, {
    acknowledgementTimeoutMs: 50,
    loginTimeoutMs: 50,
    intervalMs: 1,
  });

  assert.equal(loginSourceState(source), "complete");
  assert.equal(elementClicks, 1);
  assert.deepEqual(marks, ["ios-ui:login:submit:element-clicked"]);
});

test("native login uses one guarded pointer fallback when element click leaves the form unchanged", async () => {
  const button = { id: "login" };
  const marks = [];
  let pointerTapped = false;
  let findCalls = 0;
  const client = {
    async findElements() {
      findCalls += 1;
      return [button];
    },
    async elementRect() {
      return { x: 20, y: 600, width: 390, height: 52 };
    },
    async clickElement() {},
    async source() {
      return pointerTapped
        ? '<Button name="首页"/><Button name="记录"/>'
        : '<Text name="欢迎登录"/><Button name="账号登录"/>';
    },
    async isKeyboardShown() {
      return false;
    },
    async tapElementCenter(element) {
      assert.equal(element, button);
      pointerTapped = true;
    },
  };
  const artifacts = { mark(value) { marks.push(value); } };

  const source = await submitAccountLogin(client, artifacts, {
    acknowledgementTimeoutMs: 5,
    fallbackTimeoutMs: 50,
    intervalMs: 1,
  });

  assert.equal(loginSourceState(source), "complete");
  assert.equal(findCalls, 2);
  assert.deepEqual(marks, [
    "ios-ui:login:submit:element-clicked",
    "ios-ui:login:submit:pointer-fallback",
  ]);
});

test("native named action refinds the guarded element before its one pointer fallback", async () => {
  const button = { id: "cancel" };
  let pointerTapped = false;
  let findCalls = 0;
  const client = {
    async findElements() {
      findCalls += 1;
      return [button];
    },
    async elementRect() {
      return { x: 20, y: 700, width: 180, height: 52 };
    },
    async clickElement() {},
    async source() {
      return pointerTapped
        ? '<Button name="新建回忆录"/><Text name="当前回忆录"/>'
        : '<TextField value="E2E title"/><Button name="取消"/>';
    },
    async isKeyboardShown() {
      return false;
    },
    async tapElementCenter(element) {
      assert.equal(element, button);
      pointerTapped = true;
    },
  };
  let fallbackMarks = 0;

  const source = await tapLargestNamedElementAndWait(client, "取消", {
    description: "取消新建回忆录",
    acknowledgementTimeoutMs: 5,
    finalPredicate: (value) => value.includes("当前回忆录"),
    guardPredicate: (value) => value.includes("E2E title"),
    requireKeyboardHidden: true,
    onPointerFallback() {
      fallbackMarks += 1;
    },
  });

  assert.match(source, /当前回忆录/);
  assert.equal(findCalls, 2);
  assert.equal(fallbackMarks, 1);
});

test("native back navigation uses one iOS edge gesture after both button taps are ignored", async () => {
  const back = { id: "back" };
  const previous = '<Button name="返回"/><Text name="编辑回忆录"/>';
  let swiped = false;
  const client = {
    async findElements() {
      return [back];
    },
    async elementRect() {
      return { x: 16, y: 54, width: 30, height: 44 };
    },
    async clickElement() {},
    async tapElementCenter() {},
    async source() {
      return swiped ? '<Button name="首页"/><Button name="我的"/>' : previous;
    },
    async windowRect() {
      return { x: 0, y: 0, width: 430, height: 932 };
    },
    async swipe(fromX, fromY, toX, toY, durationMs) {
      assert.deepEqual(
        [fromX, fromY, toX, toY, durationMs],
        [5, 466, 322.5, 466, 500],
      );
      swiped = true;
    },
  };

  const source = await returnToPreviousPage(client, previous, {
    acknowledgementTimeoutMs: 5,
    gestureTimeoutMs: 50,
  });

  assert.equal(swiped, true);
  assert.match(source, /首页/);
});

test("native back navigation allows a slow WeChat hierarchy after leaving a deep page", async () => {
  const back = { id: "back" };
  const previous = '<Button name="返回"/><Text name="联系人详情"/>';
  const sourceTimeouts = [];
  const client = {
    async findElements() {
      return [back];
    },
    async elementRect() {
      return { x: 16, y: 54, width: 30, height: 44 };
    },
    async clickElement() {},
    async source(timeoutMs) {
      sourceTimeouts.push(timeoutMs);
      return '<Button name="微信"/><Button name="通讯录"/><Button name="发现"/>';
    },
  };

  const source = await returnToPreviousPage(client, previous, {
    sourceTimeoutMs: 90000,
  });

  assert.match(source, /发现/);
  assert.deepEqual(sourceTimeouts, [90000]);
});

test("native project actions tap the exact CSS action area inside its accessible card", async () => {
  const window = { x: 0, y: 0, width: 430, height: 932 };
  const cardRect = { x: 20, y: 185, width: 390, height: 199 };
  const taps = [];
  const client = {
    async windowRect() {
      return window;
    },
    async findElements() {
      return [{ id: "card" }];
    },
    async elementRect() {
      return cardRect;
    },
    async tap(x, y) {
      taps.push({ x, y });
    },
  };
  const actual = await tapProjectCardAction(
    client,
    "当前回忆录，目标回忆录",
    "edit",
  );
  assert.ok(Math.abs(actual.x - 123.52) < 0.01);
  assert.ok(Math.abs(actual.y - 341.5733333333333) < 0.01);
  assert.deepEqual(taps, [actual]);
  assert.ok(actual.x > cardRect.x && actual.x < cardRect.x + cardRect.width);
  assert.ok(actual.y > cardRect.y && actual.y < cardRect.y + cardRect.height);
  const more = projectCardActionPoint(window, cardRect, "more");
  assert.ok(Math.abs(more.x - 306.48) < 0.01);
  assert.equal(more.y, actual.y);
});

test("native account actions scroll an offscreen button into the safe viewport before tapping", async () => {
  let scrolled = false;
  const calls = [];
  const client = {
    async windowRect() {
      return { x: 0, y: 0, width: 430, height: 932 };
    },
    async findElements() {
      return [{ id: "logout" }];
    },
    async elementRect() {
      return scrolled
        ? { x: 20, y: 720, width: 390, height: 53 }
        : { x: 20, y: 962, width: 390, height: 53 };
    },
    async swipe(...args) {
      calls.push(["swipe", ...args]);
      scrolled = true;
    },
    async clickElement(element) {
      calls.push(["click", element.id]);
    },
  };
  await tapScrollableNamedElement(client, "退出登录");
  assert.equal(calls[0][0], "swipe");
  assert.deepEqual(calls[1], ["click", "logout"]);
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

test("iOS failure artifacts never persist a partially filled account login form", async () => {
  const runPath = await fs.mkdtemp(path.join(os.tmpdir(), "pawtale-ios-artifacts-"));
  try {
    const artifacts = await createIOSUIArtifacts({
      account: { email: "private@example.com", password: "private-password" },
    }, runPath);
    await artifacts.captureFailure({
      async source() {
        return '<Application name="老余口述"><Text name="欢迎登录"/>'
          + '<TextField value="partial-private"/></Application>';
      },
      async screenshot() {
        throw new Error("login screenshot must be skipped");
      },
    }, new Error("login stalled"));
    await assert.rejects(
      fs.access(path.join(runPath, "failure-source.xml")),
      { code: "ENOENT" },
    );
  } finally {
    await fs.rm(runPath, { recursive: true, force: true });
  }
});

test("iOS artifacts recognize a memoir project page without trusting an arbitrary WeChat page", () => {
  assert.equal(
    isSafePawtaleArtifactSource(
      '<Text name="我的回忆录"/><Button name="新建回忆录"/>'
        + '<Button name="当前回忆录，E2E project"/>',
    ),
    true,
  );
  assert.equal(
    isSafePawtaleArtifactSource(
      '<Text name="资料会用于语音访谈和小余追问"/>'
        + '<Text name="回忆录名称"/><Button name="取消"/><Button name="保存"/>',
    ),
    true,
  );
  assert.equal(
    isSafePawtaleArtifactSource('<Application name="微信"><Text name="新建回忆录"/></Application>'),
    false,
  );
});
