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
  readE2EConfig,
};
