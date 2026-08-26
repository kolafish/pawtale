#!/usr/bin/env node

const { spawn } = require("node:child_process");
const { readIOSRuntimeConfig } = require("../support/config");
const {
  appiumExecutable,
  ensureXCUITestDriver,
  sanitizedIOSProcessEnvironment,
} = require("../support/ios-appium-runtime");

async function readRegistryState(config) {
  try {
    const response = await fetch(`${config.tunnelRegistryUrl}/remotexpc/tunnels`, {
      signal: AbortSignal.timeout(2000),
    });
    if (!response.ok) {
      return { running: false, usbDevices: 0 };
    }
    const registry = await response.json();
    return {
      running: true,
      usbDevices: Object.values(registry?.tunnels || {}).filter(
        (tunnel) => tunnel?.connectionType === "USB",
      ).length,
    };
  } catch {
    return { running: false, usbDevices: 0 };
  }
}

(async () => {
  const config = readIOSRuntimeConfig();
  await ensureXCUITestDriver(config);
  const registry = await readRegistryState(config);
  if (registry.running && registry.usbDevices > 0) {
    process.stdout.write(
      `RemoteXPC 隧道已经运行（${registry.usbDevices} 台 USB iPhone）；`
      + "保持原 Terminal 窗口开启即可。\n",
    );
    return;
  }
  if (registry.running) {
    throw new Error(
      "RemoteXPC 注册服务正在运行，但没有 USB iPhone 隧道；请解锁并重新连接手机，"
      + "等待原隧道自动恢复。若原窗口已停止重连，请先关闭它再重新运行 ios:tunnel",
    );
  }
  const registryUrl = new URL(config.tunnelRegistryUrl);
  const port = Number(registryUrl.port || "80");
  const environment = sanitizedIOSProcessEnvironment(config);
  const args = [
    "env",
    `APPIUM_HOME=${environment.APPIUM_HOME}`,
    `DEVELOPER_DIR=${environment.DEVELOPER_DIR}`,
    `PATH=${environment.PATH}`,
    appiumExecutable(),
    "driver",
    "run",
    "xcuitest",
    "tunnel-creation",
    "--",
    "--tunnel-registry-port",
    String(port),
    "--disconnect-retry-max-attempts",
    "0",
  ];
  if (config.iosUdid) {
    args.push("--udid", config.iosUdid);
  }
  process.stdout.write(
    "即将启动 iOS RemoteXPC 隧道。若系统询问密码，请在当前 Terminal 输入 Mac 登录密码；"
    + "输入时不会显示字符。测试期间不要关闭此窗口。\n",
  );
  const child = spawn("sudo", args, { stdio: "inherit" });
  child.once("error", (error) => {
    process.stderr.write(`无法启动 sudo：${error.message}\n`);
    process.exitCode = 1;
  });
  child.once("exit", (code, signal) => {
    if (code !== 0) {
      process.stderr.write(`RemoteXPC 隧道已退出（${signal || code}）。\n`);
      process.exitCode = code || 1;
    }
  });
})().catch((error) => {
  process.stderr.write(`${error.message}\n`);
  process.exitCode = 1;
});
