const fs = require("node:fs");
const path = require("node:path");

const E2E_ROOT = path.resolve(__dirname, "..");
const REPOSITORY_ROOT = path.resolve(E2E_ROOT, "../..");
const LOCAL_ENV_PATH = path.join(E2E_ROOT, ".env.e2e.local");

if (fs.existsSync(LOCAL_ENV_PATH)) {
  process.loadEnvFile(LOCAL_ENV_PATH);
}

function integerEnvironmentValue(name, fallback) {
  const raw = String(process.env[name] || "").trim();
  if (!raw) {
    return fallback;
  }
  const value = Number(raw);
  if (!Number.isInteger(value) || value <= 0 || value > 65535) {
    throw new Error(`${name} 必须是 1 到 65535 之间的整数`);
  }
  return value;
}

function requiredAccountConfig() {
  const email = String(process.env.PAWTALE_E2E_EMAIL || "").trim();
  const password = String(process.env.PAWTALE_E2E_PASSWORD || "");
  if (!email || !password) {
    throw new Error(
      "缺少测试账号；请复制 e2e/miniprogram/.env.e2e.local.example "
      + "为 .env.e2e.local 并填写 PAWTALE_E2E_EMAIL/PAWTALE_E2E_PASSWORD",
    );
  }
  return { email, password };
}

function requiredEnvironmentValue(name) {
  const value = String(process.env[name] || "").trim();
  if (!value) {
    throw new Error(`缺少 ${name}；请填写 e2e/miniprogram/.env.e2e.local`);
  }
  return value;
}

function urlEnvironmentValue(name, fallback) {
  const value = String(process.env[name] || fallback).trim().replace(/\/$/, "");
  let parsed;
  try {
    parsed = new URL(value);
  } catch {
    throw new Error(`${name} 必须是有效的 HTTP URL`);
  }
  if (!["http:", "https:"].includes(parsed.protocol)) {
    throw new Error(`${name} 必须使用 http 或 https`);
  }
  return value;
}

function recentSlotEnvironmentValue() {
  const raw = String(process.env.PAWTALE_E2E_IOS_RECENT_SLOT || "1").trim();
  const value = Number(raw);
  if (!Number.isInteger(value) || value < 1 || value > 4) {
    throw new Error("PAWTALE_E2E_IOS_RECENT_SLOT 必须是 1 到 4 之间的整数");
  }
  return value;
}

function readIOSRuntimeConfig() {
  return {
    appiumHome: path.join(E2E_ROOT, ".appium"),
    appiumServerUrl: urlEnvironmentValue(
      "PAWTALE_E2E_APPIUM_SERVER_URL",
      "http://127.0.0.1:4723",
    ),
    artifactsPath: path.join(E2E_ROOT, "artifacts"),
    iosUdid: String(process.env.PAWTALE_E2E_IOS_UDID || "").trim(),
    recentSlot: recentSlotEnvironmentValue(),
    tunnelRegistryUrl: urlEnvironmentValue(
      "PAWTALE_E2E_IOS_TUNNEL_REGISTRY_URL",
      "http://127.0.0.1:43000",
    ),
    wdaBundleId: String(
      process.env.PAWTALE_E2E_IOS_WDA_BUNDLE_ID
      || "cn.spaceflows.pawtale.webdriveragent",
    ).trim(),
    wechatBundleId: "com.tencent.xin",
    xcodeDeveloperDir: path.resolve(
      process.env.PAWTALE_E2E_XCODE_DEVELOPER_DIR
      || "/Applications/Xcode.app/Contents/Developer",
    ),
  };
}

function readIOSNativeConfig() {
  return {
    ...readIOSRuntimeConfig(),
    account: requiredAccountConfig(),
    iosTeamId: requiredEnvironmentValue("PAWTALE_E2E_IOS_TEAM_ID"),
  };
}

function readE2EConfig() {
  const cliPath = path.resolve(
    process.env.PAWTALE_E2E_CLI_PATH
      || "/Applications/wechatwebdevtools.app/Contents/MacOS/cli",
  );
  const projectPath = path.resolve(
    process.env.PAWTALE_E2E_PROJECT_PATH || REPOSITORY_ROOT,
  );
  if (!fs.existsSync(cliPath)) {
    throw new Error(`未找到微信开发者工具 CLI：${cliPath}`);
  }
  if (!fs.existsSync(path.join(projectPath, "project.config.json"))) {
    throw new Error(`小程序项目缺少 project.config.json：${projectPath}`);
  }
  return {
    account: requiredAccountConfig(),
    artifactsPath: path.join(E2E_ROOT, "artifacts"),
    automationPort: integerEnvironmentValue("PAWTALE_E2E_AUTOMATION_PORT", undefined),
    cliPath,
    httpBaseUrl: String(
      process.env.PAWTALE_E2E_HTTP_BASE_URL || "https://pawtale-api.spaceflows.cn",
    ).replace(/\/$/, ""),
    projectPath,
    wsBaseUrl: String(
      process.env.PAWTALE_E2E_WS_BASE_URL || "wss://pawtale-api.spaceflows.cn",
    ).replace(/\/$/, ""),
  };
}

module.exports = {
  E2E_ROOT,
  LOCAL_ENV_PATH,
  REPOSITORY_ROOT,
  readIOSNativeConfig,
  readIOSRuntimeConfig,
  readE2EConfig,
  recentSlotEnvironmentValue,
  requiredEnvironmentValue,
  urlEnvironmentValue,
};
