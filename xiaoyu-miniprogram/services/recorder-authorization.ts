function privacyMessage(message = ""): string {
  const normalized = message.toLowerCase();
  if (normalized.includes("privacy api banned")) {
    return "小程序后台尚未声明录音用途，请先完成隐私保护指引配置";
  }
  if (normalized.includes("privacy") || normalized.includes("authorize")) {
    return "请先同意微信小程序隐私保护指引";
  }
  return message || "微信隐私授权失败";
}

function ensurePrivacyAuthorization(): Promise<void> {
  if (typeof wx.getPrivacySetting !== "function") {
    return Promise.resolve();
  }
  return new Promise((resolve, reject) => {
    wx.getPrivacySetting({
      success(result: { needAuthorization?: boolean }) {
        if (!result.needAuthorization || typeof wx.requirePrivacyAuthorize !== "function") {
          resolve();
          return;
        }
        wx.requirePrivacyAuthorize({
          success: resolve,
          fail(error: { errMsg?: string }) {
            reject(new Error(privacyMessage(error.errMsg)));
          },
        });
      },
      fail(error: { errMsg?: string }) {
        reject(new Error(privacyMessage(error.errMsg)));
      },
    });
  });
}

function ensureMicrophonePermission(): Promise<void> {
  return new Promise((resolve, reject) => {
    wx.authorize({
      scope: "scope.record",
      success: resolve,
      fail() {
        reject(new Error("没有麦克风权限，请在微信设置中允许使用麦克风"));
      },
    });
  });
}

export async function ensureRecorderAuthorization(): Promise<void> {
  await ensurePrivacyAuthorization();
  await ensureMicrophonePermission();
}
