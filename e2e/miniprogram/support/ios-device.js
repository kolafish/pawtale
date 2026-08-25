const { prepareE2ERuntime } = require("./session");
const { sleep, withTimeout } = require("./wait");

function assertIOSPlatform(systemInfo) {
  const platform = String(systemInfo?.platform || "").toLowerCase();
  if (platform !== "ios") {
    throw new Error(
      `真机目标要求 iOS 运行时，实际 platform=${platform || "unknown"}`,
    );
  }
}

async function connectIOSDevice(session, dependencies = {}) {
  const prepareRuntime = dependencies.prepareRuntime || prepareE2ERuntime;
  const writeLine = dependencies.writeLine || ((message) => process.stdout.write(`${message}\n`));
  const waitForStabilization = dependencies.waitForStabilization || (() => sleep(5000));
  const { artifacts, config, miniProgram } = session;
  const waitForManualPairing = dependencies.waitForManualPairing || (() => new Promise(
    (resolve) => miniProgram.connection.once("Tool.onRemoteDebugConnected", resolve),
  ));
  artifacts.mark("ios:pairing:start");
  try {
    if (config.iosSdkPairing) {
      writeLine(
        config.iosAutoConnect
          ? "正在等待微信开发者工具自动连接 iOS 真机……"
          : "请使用 iPhone 微信扫描开发者工具或终端显示的真机调试二维码……",
      );
      await withTimeout(
        miniProgram.remote(config.iosAutoConnect),
        config.iosConnectTimeoutMs,
        "等待 iOS 真机连接",
      );
    } else {
      writeLine(
        "请在开发者工具点击“真机调试 → 二维码真机调试”，待二维码生成后用 iPhone 微信扫码……",
      );
      await withTimeout(
        waitForManualPairing(),
        config.iosConnectTimeoutMs,
        "等待 iOS 真机连接",
      );
      await waitForStabilization();
    }
  } catch (error) {
    if (String(error?.message || "").includes("等待 iOS 真机连接超时")) {
      throw new Error(
        "等待 iOS 真机连接超时；请确认 iPhone 已登录有调试权限的微信，"
        + "并扫描真机调试二维码，或启用 PAWTALE_E2E_IOS_AUTO_CONNECT",
      );
    }
    throw error;
  }
  artifacts.mark("ios:pairing:connected");
  const systemInfo = await miniProgram.systemInfo();
  assertIOSPlatform(systemInfo);
  artifacts.mark("ios:platform:verified");
  await prepareRuntime(session);
  artifacts.mark("ios:runtime:prepared");
  return systemInfo;
}

module.exports = {
  assertIOSPlatform,
  connectIOSDevice,
};
