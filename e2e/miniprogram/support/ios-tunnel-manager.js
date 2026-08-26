const fs = require("node:fs");
const fsPromises = require("node:fs/promises");
const path = require("node:path");
const { spawn } = require("node:child_process");
const { E2E_ROOT } = require("./config");
const { sanitizedIOSProcessEnvironment, selectTunnel } = require("./ios-appium-runtime");
const { sleep } = require("./wait");

const TUNNEL_START_TIMEOUT_MS = 60000;

function tunnelCreationScript(config) {
  return path.join(
    config.appiumHome,
    "node_modules",
    "appium-xcuitest-driver",
    "scripts",
    "tunnel-creation.mjs",
  );
}

function supervisorScript() {
  return path.join(E2E_ROOT, "bin", "ios-tunnel-supervisor.sh");
}

async function fetchTunnelRegistry(config, fetchImpl = globalThis.fetch) {
  try {
    const response = await fetchImpl(`${config.tunnelRegistryUrl}/remotexpc/tunnels`, {
      signal: AbortSignal.timeout(2000),
    });
    if (!response.ok) {
      throw new Error(`RemoteXPC 隧道注册表返回 HTTP ${response.status}`);
    }
    return response.json();
  } catch (error) {
    if (String(error.message).includes("隧道注册表返回")) {
      throw error;
    }
    return null;
  }
}

async function runCommand(command, args, options = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd: options.cwd || E2E_ROOT,
      env: options.env || process.env,
      stdio: options.stdio || "ignore",
    });
    child.once("error", reject);
    child.once("exit", (code, signal) => resolve({ code, signal }));
  });
}

async function authorizeSudo(options = {}) {
  const run = options.runCommand || runCommand;
  const cached = await run("sudo", ["-n", "-v"]);
  if (cached.code === 0) {
    return;
  }
  if (!(options.stdinIsTTY ?? process.stdin.isTTY)) {
    throw new Error(
      "启动 iOS RemoteXPC 隧道需要 Mac 管理员密码；请在交互式 Terminal 中运行此命令",
    );
  }
  (options.write || process.stdout.write.bind(process.stdout))(
    "iOS 隧道需要一次 Mac 管理员授权。Password 后输入密码时不会显示字符。\n",
  );
  const authorized = await run("sudo", ["-v"], { stdio: "inherit" });
  if (authorized.code !== 0) {
    throw new Error("Mac 管理员授权失败，未启动 iOS RemoteXPC 隧道");
  }
}

async function processCommand(pid, options = {}) {
  return new Promise((resolve, reject) => {
    const child = (options.spawn || spawn)("ps", ["-p", String(pid), "-o", "command="], {
      stdio: ["ignore", "pipe", "pipe"],
    });
    let output = "";
    child.stdout.on("data", (chunk) => { output += chunk; });
    child.once("error", reject);
    child.once("exit", (code) => resolve(code === 0 ? output.trim() : ""));
  });
}

function parseSupervisorPid(value) {
  const pid = Number(String(value || "").trim());
  if (!Number.isSafeInteger(pid) || pid <= 1) {
    throw new Error("iOS 隧道 supervisor 未写入有效 PID");
  }
  return pid;
}

async function waitForProcessExit(pid, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (!(await processCommand(pid))) {
      return true;
    }
    await sleep(250);
  }
  return false;
}

async function stopSupervisor(pidPath, options = {}) {
  let rawPid;
  try {
    rawPid = await fsPromises.readFile(pidPath, "utf8");
  } catch (error) {
    if (error.code === "ENOENT") {
      return;
    }
    throw error;
  }
  const pid = parseSupervisorPid(rawPid);
  const command = await (options.processCommand || processCommand)(pid);
  if (!command) {
    await fsPromises.rm(pidPath, { force: true });
    return;
  }
  if (!command.includes(supervisorScript()) || !command.includes("--tunnel-registry-port")) {
    throw new Error(`拒绝停止无法确认归属的 root 进程：PID ${pid}`);
  }
  const run = options.runCommand || runCommand;
  const terminated = await run("sudo", ["-n", "kill", "-TERM", String(pid)]);
  if (terminated.code !== 0) {
    throw new Error("无法停止本轮 iOS 隧道；sudo 授权已过期");
  }
  if (!(await (options.waitForProcessExit || waitForProcessExit)(pid))) {
    const killed = await run("sudo", ["-n", "kill", "-KILL", String(pid)]);
    if (killed.code !== 0) {
      throw new Error(`无法清理 iOS 隧道 supervisor：PID ${pid}`);
    }
    if (!(await (options.waitForProcessExit || waitForProcessExit)(pid, 5000))) {
      throw new Error(`iOS 隧道 supervisor 未退出：PID ${pid}`);
    }
  }
  await fsPromises.rm(pidPath, { force: true });
}

async function waitForTunnel(config, child, fetchImpl = globalThis.fetch) {
  let startupError = null;
  child.once("error", (error) => { startupError = error; });
  const deadline = Date.now() + TUNNEL_START_TIMEOUT_MS;
  while (Date.now() < deadline) {
    if (startupError) {
      throw startupError;
    }
    if (child.exitCode !== null) {
      throw new Error("iOS RemoteXPC 隧道启动失败；请查看 tunnel.log");
    }
    const registry = await fetchTunnelRegistry(config, fetchImpl);
    if (registry) {
      try {
        return selectTunnel(registry, config.iosUdid);
      } catch (error) {
        if (!String(error.message).includes("未检测到 USB iPhone")) {
          throw error;
        }
      }
    }
    await sleep(500);
  }
  throw new Error("等待 USB iPhone RemoteXPC 隧道启动超时；请确认手机已连接并解锁");
}

async function startOwnedTunnel(config, runPath, options = {}) {
  await authorizeSudo(options);
  const pidPath = path.join(runPath, "tunnel-supervisor.pid");
  const logPath = path.join(runPath, "tunnel.log");
  const environment = sanitizedIOSProcessEnvironment(config);
  const registryUrl = new URL(config.tunnelRegistryUrl);
  const args = [
    "-n",
    supervisorScript(),
    String(process.pid),
    pidPath,
    "/usr/bin/env",
    `APPIUM_HOME=${environment.APPIUM_HOME}`,
    `DEVELOPER_DIR=${environment.DEVELOPER_DIR}`,
    `PATH=${environment.PATH}`,
    process.execPath,
    tunnelCreationScript(config),
    "--tunnel-registry-port",
    String(Number(registryUrl.port || "80")),
    "--disconnect-retry-max-attempts",
    "0",
  ];
  if (config.iosUdid) {
    args.push("--udid", config.iosUdid);
  }
  const logDescriptor = fs.openSync(logPath, "a");
  let child;
  try {
    child = (options.spawn || spawn)("sudo", args, {
      cwd: E2E_ROOT,
      env: process.env,
      stdio: ["ignore", logDescriptor, logDescriptor],
    });
  } finally {
    fs.closeSync(logDescriptor);
  }
  const keepSudoAlive = setInterval(() => {
    void (options.runCommand || runCommand)("sudo", ["-n", "-v"]).catch(() => {});
  }, 60000);
  keepSudoAlive.unref();
  try {
    const tunnel = await waitForTunnel(config, child, options.fetch || globalThis.fetch);
    let stopped = false;
    return {
      logPath,
      owned: true,
      tunnel,
      async stop() {
        if (stopped) {
          return;
        }
        stopped = true;
        clearInterval(keepSudoAlive);
        await stopSupervisor(pidPath, options);
      },
      wait() {
        if (child.exitCode !== null) {
          return Promise.resolve({ code: child.exitCode, signal: child.signalCode });
        }
        return new Promise((resolve) => child.once("exit", (code, signal) => resolve({ code, signal })));
      },
    };
  } catch (error) {
    clearInterval(keepSudoAlive);
    let cleanupError = null;
    try {
      await stopSupervisor(pidPath, options);
    } catch (caught) {
      cleanupError = caught;
    }
    if (cleanupError) {
      throw new AggregateError(
        [error, cleanupError],
        "iOS RemoteXPC 隧道启动失败且未能完成清理",
      );
    }
    throw error;
  }
}

async function ensureRemoteXPCTunnel(config, runPath, options = {}) {
  const registry = await fetchTunnelRegistry(config, options.fetch || globalThis.fetch);
  if (registry) {
    const tunnel = selectTunnel(registry, config.iosUdid);
    return {
      owned: false,
      tunnel,
      stop: async () => {},
      wait: async () => ({ code: 0, signal: null }),
    };
  }
  process.stdout.write("未发现现有 iOS 隧道，正在按需启动……\n");
  return startOwnedTunnel(config, runPath, options);
}

module.exports = {
  TUNNEL_START_TIMEOUT_MS,
  authorizeSudo,
  ensureRemoteXPCTunnel,
  fetchTunnelRegistry,
  parseSupervisorPid,
  processCommand,
  startOwnedTunnel,
  stopSupervisor,
  supervisorScript,
  tunnelCreationScript,
  waitForTunnel,
};
