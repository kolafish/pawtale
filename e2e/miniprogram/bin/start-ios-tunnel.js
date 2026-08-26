#!/usr/bin/env node

const fs = require("node:fs/promises");
const path = require("node:path");
const { readIOSRuntimeConfig } = require("../support/config");
const {
  ensureXCUITestDriver,
} = require("../support/ios-appium-runtime");
const { ensureRemoteXPCTunnel } = require("../support/ios-tunnel-manager");

(async () => {
  const config = readIOSRuntimeConfig();
  await ensureXCUITestDriver(config);
  const runtimePath = path.join(config.appiumHome, "runtime");
  await fs.mkdir(runtimePath, { recursive: true });
  const tunnel = await ensureRemoteXPCTunnel(config, runtimePath);
  if (!tunnel.owned) {
    process.stdout.write(
      "RemoteXPC 隧道已经运行（1 台 USB iPhone）；"
      + "保持原 Terminal 窗口开启即可。\n",
    );
    return;
  }
  process.stdout.write(
    `RemoteXPC 隧道已启动：${tunnel.tunnel.udid}。按 Ctrl-C 可安全停止。\n`,
  );
  let stopping = false;
  const stop = async () => {
    if (stopping) {
      return;
    }
    stopping = true;
    await tunnel.stop();
  };
  for (const signal of ["SIGINT", "SIGTERM", "SIGHUP"]) {
    process.once(signal, () => {
      void stop().catch((error) => {
        process.stderr.write(`${error.message}\n`);
        process.exitCode = 1;
        // Exiting lets the root supervisor watchdog observe that its owner is
        // gone and perform the same bounded cleanup without a sudo ticket.
        process.exit();
      });
    });
  }
  const result = await tunnel.wait();
  if (!stopping) {
    await tunnel.stop();
    throw new Error(`RemoteXPC 隧道意外退出（${result.signal || result.code || "0"}）`);
  }
  if (stopping) {
    process.stdout.write("RemoteXPC 隧道已安全停止。\n");
  }
})().catch((error) => {
  process.stderr.write(`${error.message}\n`);
  process.exitCode = 1;
});
