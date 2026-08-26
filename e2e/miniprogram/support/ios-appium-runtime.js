const fs = require("node:fs");
const fsPromises = require("node:fs/promises");
const path = require("node:path");
const { spawn } = require("node:child_process");
const { E2E_ROOT } = require("./config");
const { sleep } = require("./wait");

const XCUITEST_DRIVER_VERSION = "12.8.0";

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
      env: sanitizedIOSProcessEnvironment(config),
      stdio: ["ignore", logDescriptor, logDescriptor],
    });
  } finally {
    fs.closeSync(logDescriptor);
  }
  let startupError = null;
  child.once("error", (error) => { startupError = error; });
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
          if (child.exitCode === null) {
            await new Promise((resolve) => {
              const timer = setTimeout(() => child.kill("SIGKILL"), 5000);
              child.once("exit", () => {
                clearTimeout(timer);
                resolve();
              });
              child.kill("SIGTERM");
            });
          }
        },
      };
    }
    await sleep(500);
  }
  child.kill("SIGTERM");
  throw new Error(`等待 Appium server 启动超时，日志：${logPath}`);
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
  isAppiumReady,
  parseJSONOutput,
  readTunnel,
  runAppiumCommand,
  sanitizedIOSProcessEnvironment,
  selectTunnel,
  startAppiumServer,
};
