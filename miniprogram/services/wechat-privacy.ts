const PRIVACY_API_BANNED_MESSAGE = "小程序后台还没有声明录音隐私用途，请在微信公众平台完成《用户隐私保护指引》的麦克风/录音用途声明后重新提交审核。";
const PRIVACY_AUTH_REQUIRED_MESSAGE = "请先同意微信小程序隐私保护指引后再开始录音。";
const RECORDER_SYNC_ERROR_MESSAGE = "麦克风启动失败，请稍后重试。若刚才正在播放小余的声音，请等一秒再点开始说。";
const RECORDER_BUSY_MESSAGE = "麦克风正在使用中，请稍后重试。";
const RECORDER_PERMISSION_DENIED_MESSAGE = "没有麦克风权限，请在小程序设置中允许录音；若已允许，请在手机系统设置里允许微信使用麦克风。";
const RECORDER_PERMISSION_DENIED_CODE = "recorder_permission_denied";
const RECORDER_SCOPE = "scope.record";

const LOGIN_PRIVACY_CONSENT_KEY = "pawtale_login_privacy_consent";
// Bump when the login privacy contract changes and needs renewed consent.
const LOGIN_PRIVACY_CONTRACT_VERSION = "2026-10-25";

export function readLoginPrivacyConsent(): boolean {
  try {
    return wx.getStorageSync<unknown>(LOGIN_PRIVACY_CONSENT_KEY) === LOGIN_PRIVACY_CONTRACT_VERSION;
  } catch {
    return false;
  }
}

export function saveLoginPrivacyConsent(agreed: boolean): void {
  if (agreed) {
    wx.setStorageSync(LOGIN_PRIVACY_CONSENT_KEY, LOGIN_PRIVACY_CONTRACT_VERSION);
  } else {
    wx.removeStorageSync(LOGIN_PRIVACY_CONSENT_KEY);
  }
}

interface RecorderPermissionError extends Error {
  code: typeof RECORDER_PERMISSION_DENIED_CODE;
}

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

export function wechatRecorderErrorMessage(errMsg?: string): string {
  const message = normalizeMessage(errMsg);
  if (message.includes("privacy")) {
    return wechatPrivacyErrorMessage(errMsg);
  }
  if (isRecorderPermissionDeniedMessage(errMsg)) {
    return RECORDER_PERMISSION_DENIED_MESSAGE;
  }
  if (message.includes("is recording") || message.includes("busy") || message.includes("occupied")) {
    return RECORDER_BUSY_MESSAGE;
  }
  if (message.includes("received error code") || message.includes("sync-")) {
    return RECORDER_SYNC_ERROR_MESSAGE;
  }
  return errMsg || "录音启动失败，请重试";
}

export function isRecorderPermissionDeniedMessage(value?: string): boolean {
  const message = normalizeMessage(value);
  return !message.includes("privacy") && (message.includes("auth deny")
    || message.includes("permission")
    || message.includes("authorize")
    || message.includes("没有麦克风权限")
    || message.includes("麦克风权限仍未开启"));
}

function recorderPermissionDeniedError(): RecorderPermissionError {
  const error = new Error(RECORDER_PERMISSION_DENIED_MESSAGE) as RecorderPermissionError;
  error.code = RECORDER_PERMISSION_DENIED_CODE;
  return error;
}

export function isRecorderPermissionDeniedError(error: unknown): boolean {
  if (!error || typeof error !== "object") {
    return false;
  }
  const candidate = error as { code?: string; message?: string };
  return candidate.code === RECORDER_PERMISSION_DENIED_CODE
    || isRecorderPermissionDeniedMessage(candidate.message);
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

function recorderAuthorizationSetting(): Promise<boolean | undefined> {
  const getSettingApi = wx.getSetting;
  if (typeof getSettingApi !== "function") {
    return Promise.resolve(undefined);
  }
  return new Promise((resolve, reject) => {
    getSettingApi({
      success: (result) => resolve(result.authSetting[RECORDER_SCOPE]),
      fail: (error) => reject(new Error(error.errMsg || "麦克风权限读取失败")),
    });
  });
}

async function ensureRecorderScopeAuthorization(): Promise<void> {
  const authorized = await recorderAuthorizationSetting();
  if (authorized === true) {
    return;
  }
  if (authorized === false) {
    throw recorderPermissionDeniedError();
  }
  const authorizeApi = wx.authorize;
  if (typeof authorizeApi !== "function") {
    return;
  }
  await new Promise<void>((resolve, reject) => {
    authorizeApi({
      scope: RECORDER_SCOPE,
      success: () => resolve(),
      fail: (error) => reject(
        isRecorderPermissionDeniedMessage(error.errMsg)
          ? recorderPermissionDeniedError()
          : new Error(wechatRecorderErrorMessage(error.errMsg)),
      ),
    });
  });
}

export async function ensureRecorderPrivacyAuthorization(): Promise<void> {
  const needAuthorization = await getPrivacySetting();
  if (needAuthorization) {
    await requirePrivacyAuthorize();
  }
  await ensureRecorderScopeAuthorization();
}

export function openRecorderPermissionSettings(): Promise<boolean> {
  const openSettingApi = wx.openSetting;
  if (typeof openSettingApi !== "function") {
    return Promise.reject(new Error("当前微信版本无法打开权限设置，请在微信或手机系统设置中允许麦克风。"));
  }
  return new Promise((resolve, reject) => {
    openSettingApi({
      success: (result) => resolve(result.authSetting[RECORDER_SCOPE] === true),
      fail: (error) => reject(new Error(error.errMsg || "麦克风设置打开失败")),
    });
  });
}

export function openWechatPrivacyContract(): Promise<void> {
  const openPrivacyContract = wx.openPrivacyContract;
  if (typeof openPrivacyContract !== "function") {
    return Promise.reject(new Error("当前微信版本暂不支持查看隐私保护指引，请更新微信后重试。"));
  }
  return new Promise((resolve, reject) => {
    openPrivacyContract({
      success: () => resolve(),
      fail: () => reject(new Error("暂时无法打开隐私保护指引，请稍后再试。")),
    });
  });
}
