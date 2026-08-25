const MiniProgram = require("miniprogram-automator/out/MiniProgram").default;

const originalCheckVersion = MiniProgram.prototype.checkVersion;

// Developer Tools 2.02 returns Tool.getInfo.version instead of the historical
// SDKVersion field expected by miniprogram-automator 0.12.1. Keep the official
// SDK and isolate the protocol compatibility here until it accepts both fields.
MiniProgram.prototype.checkVersion = async function checkDeveloperToolsVersion() {
  const info = await this.connection.send("Tool.getInfo");
  if (info?.SDKVersion) {
    return originalCheckVersion.call(this);
  }
  if (typeof info?.version === "string" && info.version.trim()) {
    return;
  }
  throw new Error("微信开发者工具未返回可识别的版本信息");
};

module.exports = require("miniprogram-automator");
