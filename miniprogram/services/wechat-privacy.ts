const PRIVACY_API_BANNED_MESSAGE = "小程序后台还没有声明录音隐私用途，请在微信公众平台完成《用户隐私保护指引》的麦克风/录音用途声明后重新提交审核。";
const PRIVACY_AUTH_REQUIRED_MESSAGE = "请先同意微信小程序隐私保护指引后再开始录音。";

function normalizeMessage(value?: string): string {
  return String(value || "").toLowerCase();
}

export function wechatPrivacyErrorMessage(errMsg?: string): string {
  const message = normalizeMessage(errMsg);
  if (message.includes("privacy api banned")) {
    return PRIVACY_API_BANNED_MESSAGE;
  }
  if (message.includes("privacy") || message.includes("authorize")) {
    return PRIVACY_AUTH_REQUIRED_MESSAGE;
  }
  return errMsg || "微信隐私授权失败";
}

function getPrivacySetting(): Promise<boolean> {
  const getPrivacySettingApi = wx.getPrivacySetting;
  if (typeof getPrivacySettingApi !== "function") {
    return Promise.resolve(false);
  }
  return new Promise((resolve, reject) => {
    getPrivacySettingApi({
      success: (result) => resolve(Boolean(result.needAuthorization)),
      fail: (error) => reject(new Error(wechatPrivacyErrorMessage(error.errMsg))),
    });
  });
}

function requirePrivacyAuthorize(): Promise<void> {
  const requirePrivacyAuthorizeApi = wx.requirePrivacyAuthorize;
  if (typeof requirePrivacyAuthorizeApi !== "function") {
    return Promise.resolve();
  }
  return new Promise((resolve, reject) => {
    requirePrivacyAuthorizeApi({
      success: () => resolve(),
      fail: (error) => reject(new Error(wechatPrivacyErrorMessage(error.errMsg))),
    });
  });
}

export async function ensureRecorderPrivacyAuthorization(): Promise<void> {
  const needAuthorization = await getPrivacySetting();
  if (!needAuthorization) {
    return;
  }
  await requirePrivacyAuthorize();
}
