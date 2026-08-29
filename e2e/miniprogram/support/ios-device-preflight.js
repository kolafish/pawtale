const os = require("node:os");
const fs = require("node:fs/promises");
const path = require("node:path");
const { execFile } = require("node:child_process");
const { promisify } = require("node:util");

const execFileAsync = promisify(execFile);

async function validateBackendAccount(config, fetchImpl = globalThis.fetch) {
  let health;
  try {
    health = await fetchImpl(`${config.httpBaseUrl}/health`, {
      signal: AbortSignal.timeout(10000),
    });
  } catch {
    throw new Error(`无法连接真机测试后端：${config.httpBaseUrl}`);
  }
  if (!health.ok) {
    throw new Error(`真机测试后端健康检查失败：HTTP ${health.status}`);
  }

  let login;
  try {
    login = await fetchImpl(`${config.httpBaseUrl}/api/auth/login`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(config.account),
      signal: AbortSignal.timeout(15000),
    });
  } catch {
    throw new Error("无法验证真机测试账号；后端登录请求未完成");
  }
  if (!login.ok) {
    throw new Error(`真机测试账号验证失败：HTTP ${login.status}`);
  }
  return {
    accountVerified: true,
    backendHost: new URL(config.httpBaseUrl).host,
  };
}

async function validateIOSDeviceEnvironment(config, options = {}) {
  const access = options.access || fs.access;
  const fetchImpl = options.fetch || globalThis.fetch;
  const tunnelScriptPath = options.tunnelScriptPath;
  const requiredPaths = [
    path.join(config.xcodeDeveloperDir, "usr", "bin", "xcodebuild"),
    tunnelScriptPath,
  ];
  for (const requiredPath of requiredPaths) {
    try {
      await access(requiredPath);
    } catch {
      throw new Error(`iOS 真机环境缺少文件：${requiredPath}`);
    }
  }
  return validateBackendAccount(config, fetchImpl);
}

async function readIOSDeviceLockState(config, udid, options = {}) {
  if (!udid) {
    throw new Error("读取 iPhone 锁定状态时缺少设备 UDID");
  }
  const temporaryPath = await (options.mkdtemp || fs.mkdtemp)(
    path.join(os.tmpdir(), "pawtale-ios-lock-state-"),
  );
  const outputPath = path.join(temporaryPath, "lock-state.json");
  try {
    const run = options.execFile || execFileAsync;
    await run("/usr/bin/xcrun", [
      "devicectl",
      "device",
      "info",
      "lockState",
      "--quiet",
      "--json-output",
      outputPath,
      "--device",
      udid,
    ], {
      env: {
        ...process.env,
        DEVELOPER_DIR: config.xcodeDeveloperDir,
      },
      timeout: 15000,
    });
    const payload = JSON.parse(await (options.readFile || fs.readFile)(outputPath, "utf8"));
    if (
      payload?.info?.outcome !== "success"
      || typeof payload?.result?.passcodeRequired !== "boolean"
    ) {
      throw new Error("devicectl 未返回有效的 iPhone 锁定状态");
    }
    return payload.result;
  } catch (error) {
    throw new Error(
      "无法读取 iPhone 锁定状态；请确认手机已连接且 Xcode 可识别该设备",
      { cause: error },
    );
  } finally {
    await (options.rm || fs.rm)(temporaryPath, { recursive: true, force: true });
  }
}

async function validateIOSDeviceUnlocked(config, udid, options = {}) {
  const readLockState = options.readLockState
    || ((deviceUdid) => readIOSDeviceLockState(config, deviceUdid, options));
  const lockState = await readLockState(udid);
  if (lockState.passcodeRequired) {
    throw new Error("iPhone 已锁定；请解锁手机并保持屏幕亮着，再运行 iOS 真机测试");
  }
  return { deviceUnlocked: true };
}

module.exports = {
  readIOSDeviceLockState,
  validateBackendAccount,
  validateIOSDeviceEnvironment,
  validateIOSDeviceUnlocked,
};
