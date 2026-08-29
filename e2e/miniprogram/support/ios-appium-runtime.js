const fs = require("node:fs");
const fsPromises = require("node:fs/promises");
const path = require("node:path");
const { execFile, spawn } = require("node:child_process");
const { promisify } = require("node:util");
const { E2E_ROOT } = require("./config");
const { sleep } = require("./wait");

const XCUITEST_DRIVER_VERSION = "12.8.0";
const execFileAsync = promisify(execFile);

function appiumExecutable() {
  return path.join(E2E_ROOT, "node_modules", ".bin", "appium");
}

function sanitizedIOSProcessEnvironment(config) {
  const environment = {
    ...process.env,
    APPIUM_HOME: config.appiumHome,
    DEVELOPER_DIR: config.xcodeDeveloperDir,
    PATH: [
      "/usr/bin",
      "/bin",
      "/usr/sbin",
      "/sbin",
      process.env.PATH || "",
    ].join(":"),
  };
  for (const name of [
    "CC",
    "CFLAGS",
    "CPP",
    "CPPFLAGS",
    "CXX",
    "CXXFLAGS",
    "LDFLAGS",
    "SDKROOT",
  ]) {
    delete environment[name];
  }
  return environment;
}

async function runAppiumCommand(config, args, options = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(appiumExecutable(), args, {
      cwd: E2E_ROOT,
      env: sanitizedIOSProcessEnvironment(config),
      stdio: options.inherit ? "inherit" : ["ignore", "pipe", "pipe"],
    });
    if (options.inherit) {
      child.once("error", reject);
      child.once("exit", (code, signal) => resolve({ code, signal }));
      return;
    }
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => { stdout += chunk; });
    child.stderr.on("data", (chunk) => { stderr += chunk; });
    child.once("error", reject);
    child.once("exit", (code, signal) => {
      if (code === 0) {
        resolve({ code, signal, stdout, stderr });
      } else {
        reject(new Error(
          `Appium 命令失败（${args.join(" ")}）：${stderr || stdout || signal || code}`,
        ));
      }
    });
  });
}

function parseJSONOutput(output) {
  const start = output.indexOf("{");
  if (start === -1) {
    throw new Error("Appium 命令未返回 JSON");
  }
  let depth = 0;
  let escaped = false;
  let inString = false;
  for (let index = start; index < output.length; index += 1) {
    const character = output[index];
    if (inString) {
      if (escaped) {
        escaped = false;
      } else if (character === "\\") {
        escaped = true;
      } else if (character === '"') {
        inString = false;
      }
      continue;
    }
    if (character === '"') {
      inString = true;
    } else if (character === "{") {
      depth += 1;
    } else if (character === "}") {
      depth -= 1;
      if (depth === 0) {
        return JSON.parse(output.slice(start, index + 1));
      }
    }
  }
  throw new Error("Appium 命令返回了不完整 JSON");
}

async function ensureXCUITestDriver(config) {
  await fsPromises.mkdir(config.appiumHome, { recursive: true });
  const listed = await runAppiumCommand(config, ["driver", "list", "--installed", "--json"]);
  const drivers = parseJSONOutput(`${listed.stdout}\n${listed.stderr}`);
  if (!drivers.xcuitest) {
    process.stdout.write(`正在安装 XCUITest driver ${XCUITEST_DRIVER_VERSION}……\n`);
    await runAppiumCommand(config, [
      "driver",
      "install",
      `xcuitest@${XCUITEST_DRIVER_VERSION}`,
    ], { inherit: true });
    return;
  }
  if (drivers.xcuitest.version !== XCUITEST_DRIVER_VERSION) {
    throw new Error(
      `当前 XCUITest driver=${drivers.xcuitest.version}，`
      + `测试要求 ${XCUITEST_DRIVER_VERSION}；请删除 e2e/miniprogram/.appium 后重新运行 ios:setup`,
    );
  }
}

async function isAppiumReady(serverUrl, fetchImpl = globalThis.fetch) {
  try {
    const response = await fetchImpl(`${serverUrl}/status`, {
      signal: AbortSignal.timeout(2000),
    });
    const payload = await response.json();
    return response.ok && payload?.value?.ready === true;
  } catch {
    return false;
  }
}

function processGroupExists(processGroupId, kill = process.kill) {
  try {
    kill(-processGroupId, 0);
    return true;
  } catch (error) {
    if (error.code === "ESRCH") {
      return false;
    }
    if (error.code === "EPERM") {
      // The original user-owned group has exited. A protected system helper may
      // briefly retain the same process group, but it is outside this runner's ownership.
      return false;
    }
    throw error;
  }
}

async function waitForProcessGroupExit(processGroupId, timeoutMs, options = {}) {
  const exists = options.processGroupExists || processGroupExists;
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (!exists(processGroupId, options.kill)) {
      return true;
    }
    await (options.sleep || sleep)(100);
  }
  return !exists(processGroupId, options.kill);
}

async function stopOwnedProcessGroup(processGroupId, options = {}) {
  const kill = options.kill || process.kill;
  if (!processGroupExists(processGroupId, kill)) {
    return;
  }
  kill(-processGroupId, "SIGTERM");
  if (await waitForProcessGroupExit(processGroupId, 5000, options)) {
    return;
  }
  kill(-processGroupId, "SIGKILL");
  if (!(await waitForProcessGroupExit(processGroupId, 5000, options))) {
    throw new Error(`Appium 进程组未退出：${processGroupId}`);
  }
}

function parseProcessTable(output) {
  return String(output || "")
    .split("\n")
    .map((line) => line.match(/^\s*(\d+)\s+(\d+)\s+(\d+)\s+(.+)$/))
    .filter(Boolean)
    .map((match) => ({
      pid: Number(match[1]),
      ppid: Number(match[2]),
      processGroupId: Number(match[3]),
      command: match[4],
    }));
}

function descendantProcessGroups(rootPid, processes) {
  const descendantIds = new Set([rootPid]);
  let added = true;
  while (added) {
    added = false;
    for (const processInfo of processes) {
      if (descendantIds.has(processInfo.ppid) && !descendantIds.has(processInfo.pid)) {
        descendantIds.add(processInfo.pid);
        added = true;
      }
    }
  }
  return [...new Set(processes
    .filter((processInfo) => (
      processInfo.pid !== rootPid
      && descendantIds.has(processInfo.pid)
      && processInfo.processGroupId > 1
      && processInfo.processGroupId !== rootPid
    ))
    .map((processInfo) => processInfo.processGroupId))];
}

async function readProcessTable(options = {}) {
  const run = options.execFile || execFileAsync;
  const result = await run("ps", ["-axo", "pid=,ppid=,pgid=,command="]);
  return parseProcessTable(typeof result === "string" ? result : result.stdout);
}

async function stopOwnedProcessTree(rootPid, options = {}) {
  const processes = await (options.readProcessTable || readProcessTable)(options);
  const childGroups = descendantProcessGroups(rootPid, processes);
  for (const processGroupId of childGroups.reverse()) {
    await stopOwnedProcessGroup(processGroupId, options);
  }
  await stopOwnedProcessGroup(rootPid, options);
}

async function startAppiumServer(config, runPath) {
  if (await isAppiumReady(config.appiumServerUrl)) {
    return { owned: false, stop: async () => {} };
  }
  const url = new URL(config.appiumServerUrl);
  if (!["127.0.0.1", "localhost"].includes(url.hostname) || url.protocol !== "http:") {
    throw new Error(`无法连接远程 Appium server：${config.appiumServerUrl}`);
  }
  const port = Number(url.port || "80");
  const logPath = path.join(runPath, "appium.log");
  const logDescriptor = fs.openSync(logPath, "a");
  let child;
  try {
    child = spawn(appiumExecutable(), [
      "--address",
      url.hostname,
      "--port",
      String(port),
      "--log-level",
      "warn",
      "--log-no-colors",
    ], {
      cwd: E2E_ROOT,
      detached: true,
      env: sanitizedIOSProcessEnvironment(config),
      stdio: ["ignore", logDescriptor, logDescriptor],
    });
  } finally {
    fs.closeSync(logDescriptor);
  }
  let startupError = null;
  child.once("error", (error) => { startupError = error; });
  try {
    const deadline = Date.now() + 60000;
    while (Date.now() < deadline) {
      if (startupError) {
        throw startupError;
      }
      if (child.exitCode !== null) {
        throw new Error(`Appium server 启动失败，日志：${logPath}`);
      }
      if (await isAppiumReady(config.appiumServerUrl)) {
        return {
          owned: true,
          async stop() {
            await stopOwnedProcessTree(child.pid);
          },
        };
      }
      await sleep(500);
    }
    throw new Error(`等待 Appium server 启动超时，日志：${logPath}`);
  } catch (error) {
    try {
      await stopOwnedProcessTree(child.pid);
    } catch (cleanupError) {
      throw new AggregateError(
        [error, cleanupError],
        "Appium server 启动失败且未能清理进程组",
      );
    }
    throw error;
  }
}

function selectTunnel(registry, requestedUdid = "") {
  const tunnels = Object.values(registry?.tunnels || {}).filter(
    (tunnel) => tunnel?.connectionType === "USB" && tunnel?.udid,
  );
  if (requestedUdid) {
    const selected = tunnels.find((tunnel) => tunnel.udid === requestedUdid);
    if (!selected) {
      throw new Error(`RemoteXPC 隧道中未找到指定 iPhone：${requestedUdid}`);
    }
    return selected;
  }
  if (tunnels.length === 0) {
    throw new Error(
      "未检测到 USB iPhone 的 RemoteXPC 隧道；请在另一个 Terminal 运行 "
      + "npm --prefix e2e/miniprogram run ios:tunnel，并保持窗口开启",
    );
  }
  if (tunnels.length > 1) {
    throw new Error(
      "检测到多个 USB iPhone 隧道；请设置 PAWTALE_E2E_IOS_UDID 选择测试设备",
    );
  }
  return tunnels[0];
}

async function readTunnel(config, fetchImpl = globalThis.fetch) {
  let response;
  try {
    response = await fetchImpl(`${config.tunnelRegistryUrl}/remotexpc/tunnels`, {
      signal: AbortSignal.timeout(5000),
    });
  } catch {
    throw new Error(
      "无法连接 RemoteXPC 隧道注册表；请运行 "
      + "npm --prefix e2e/miniprogram run ios:tunnel",
    );
  }
  if (!response.ok) {
    throw new Error(`RemoteXPC 隧道注册表返回 HTTP ${response.status}`);
  }
  return selectTunnel(await response.json(), config.iosUdid);
}

module.exports = {
  XCUITEST_DRIVER_VERSION,
  appiumExecutable,
  ensureXCUITestDriver,
  descendantProcessGroups,
  isAppiumReady,
  parseJSONOutput,
  readTunnel,
  runAppiumCommand,
  sanitizedIOSProcessEnvironment,
  selectTunnel,
  startAppiumServer,
  stopOwnedProcessGroup,
  stopOwnedProcessTree,
};
