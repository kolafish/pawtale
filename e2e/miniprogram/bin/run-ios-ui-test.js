#!/usr/bin/env node

const fs = require("node:fs/promises");
const path = require("node:path");
const { spawn } = require("node:child_process");
const { E2E_ROOT, readIOSNativeConfig } = require("../support/config");
const {
  ensureXCUITestDriver,
  readTunnel,
  startAppiumServer,
} = require("../support/ios-appium-runtime");

function safeTimestamp() {
  return new Date().toISOString().replace(/[:.]/g, "-");
}

async function runTests(environment) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [
      "--test",
      "--test-concurrency=1",
      "device-specs/ios-ui.test.js",
    ], {
      cwd: E2E_ROOT,
      env: environment,
      stdio: "inherit",
    });
    child.once("error", reject);
    child.once("exit", (code, signal) => resolve({ code, signal }));
  });
}

(async () => {
  const config = readIOSNativeConfig();
  const runPath = path.join(config.artifactsPath, `ios-ui-${safeTimestamp()}`);
  await fs.mkdir(runPath, { recursive: true });
  await ensureXCUITestDriver(config);
  const tunnel = await readTunnel(config);
  process.stdout.write(`已连接 iOS USB 真机：${tunnel.udid}\n`);
  const server = await startAppiumServer(config, runPath);
  try {
    const result = await runTests({
      ...process.env,
      PAWTALE_E2E_IOS_RUN_PATH: runPath,
      PAWTALE_E2E_IOS_UDID: tunnel.udid,
    });
    process.stdout.write(`iOS 真机产物：${runPath}\n`);
    if (result.code !== 0) {
      process.exitCode = result.code || 1;
    }
  } finally {
    await server.stop();
  }
})().catch((error) => {
  process.stderr.write(`${error.message}\n`);
  process.exitCode = 1;
});
