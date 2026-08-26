const fs = require("node:fs/promises");
const path = require("node:path");

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

module.exports = {
  validateBackendAccount,
  validateIOSDeviceEnvironment,
};
