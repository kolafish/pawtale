#!/usr/bin/env node

const fs = require("node:fs/promises");
const path = require("node:path");
const { spawn } = require("node:child_process");
const { redactValue } = require("../support/artifacts");
const { E2E_ROOT, readIOSNativeConfig } = require("../support/config");
const {
  ensureXCUITestDriver,
  startAppiumServer,
} = require("../support/ios-appium-runtime");
const { validateIOSDeviceEnvironment } = require("../support/ios-device-preflight");
const {
  ensureRemoteXPCTunnel,
  tunnelCreationScript,
} = require("../support/ios-tunnel-manager");

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

async function runStep(report, name, secrets, callback) {
  const step = { name, startedAt: new Date().toISOString() };
  const startedAt = Date.now();
  report.steps.push(step);
  try {
    const value = await callback();
    step.durationMs = Date.now() - startedAt;
    step.status = "passed";
    return value;
  } catch (error) {
    step.durationMs = Date.now() - startedAt;
    step.error = redactValue(error, secrets);
    step.status = "failed";
    throw error;
  }
}

(async () => {
  const config = readIOSNativeConfig();
  const secrets = [config.account.email, config.account.password];
  const runPath = path.join(config.artifactsPath, `ios-ui-${safeTimestamp()}`);
  await fs.mkdir(runPath, { recursive: true });
  const report = {
    finishedAt: null,
    passed: false,
    startedAt: new Date().toISOString(),
    steps: [],
    target: "ios-ui-runner",
    tunnelMode: null,
  };
  let failure = null;
  let server = null;
  let tunnelHandle = null;
  try {
    await runStep(report, "xcuitest-driver", secrets, () => ensureXCUITestDriver(config));
    report.environment = await runStep(
      report,
      "environment-preflight",
      secrets,
      () => validateIOSDeviceEnvironment(config, {
        tunnelScriptPath: tunnelCreationScript(config),
      }),
    );
    tunnelHandle = await runStep(
      report,
      "remotexpc-tunnel",
      secrets,
      () => ensureRemoteXPCTunnel(config, runPath),
    );
    report.tunnelMode = tunnelHandle.owned ? "managed" : "reused";
    process.stdout.write(`已连接 iOS USB 真机：${tunnelHandle.tunnel.udid}\n`);
    server = await runStep(
      report,
      "appium-server",
      secrets,
      () => startAppiumServer(config, runPath),
    );
    await runStep(report, "ios-ui-account-projects", secrets, async () => {
      const result = await runTests({
        ...process.env,
        PAWTALE_E2E_IOS_RUN_PATH: runPath,
        PAWTALE_E2E_IOS_UDID: tunnelHandle.tunnel.udid,
      });
      if (result.code !== 0) {
        throw new Error(`iOS 真机测试进程退出：${result.signal || result.code || "unknown"}`);
      }
    });
  } catch (error) {
    failure = error;
    process.exitCode = 1;
  } finally {
    for (const [name, resource] of [
      ["appium-server-cleanup", server],
      ["remotexpc-tunnel-cleanup", tunnelHandle],
    ]) {
      if (!resource) {
        continue;
      }
      try {
        await runStep(report, name, secrets, () => resource.stop());
      } catch (error) {
        failure ||= error;
        process.exitCode = 1;
      }
    }
    report.error = failure ? redactValue(failure, secrets) : null;
    report.finishedAt = new Date().toISOString();
    report.passed = !failure;
    await fs.writeFile(
      path.join(runPath, "runner.json"),
      `${JSON.stringify(report, null, 2)}\n`,
    );
    process.stdout.write(`iOS 真机产物：${runPath}\n`);
    if (failure) {
      process.stderr.write(`${failure.message}\n`);
    }
  }
})().catch((error) => {
  process.stderr.write(`${error.message}\n`);
  process.exitCode = 1;
});
