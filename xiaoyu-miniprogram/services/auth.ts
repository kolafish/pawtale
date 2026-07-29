import {
  ApiError,
  clearSessionCookie,
  readSessionCookie,
  request,
  type RequestOptions,
} from "./request";

export interface BackendAuthUser {
  id: string;
  display_name: string;
}

export interface AuthResponse {
  user: BackendAuthUser;
  needs_credentials: boolean;
}

let sessionPromise: Promise<void> | null = null;

function wxLoginCode(): Promise<string> {
  return new Promise((resolve, reject) => {
    wx.login({
      success(result: { code?: string }) {
        if (result.code) {
          resolve(result.code);
          return;
        }
        reject(new ApiError(0, "微信登录未返回 code", result));
      },
      fail(error: { errMsg?: string }) {
        reject(new ApiError(0, error.errMsg || "微信登录失败", error));
      },
    });
  });
}

export async function loginWithWeChat(): Promise<AuthResponse> {
  const code = await wxLoginCode();
  return request<AuthResponse>("/api/auth/wechat-login", {
    method: "POST",
    data: { code, miniprogram: "xiaoyu" },
  });
}

export async function ensureBackendSession(): Promise<void> {
  if (readSessionCookie()) {
    return;
  }
  if (!sessionPromise) {
    sessionPromise = loginWithWeChat()
      .then(() => undefined)
      .finally(() => {
        sessionPromise = null;
      });
  }
  await sessionPromise;
}

export async function requestWithBackendSession<T>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  await ensureBackendSession();
  const attemptedCookie = readSessionCookie();
  try {
    return await request<T>(path, options);
  } catch (error) {
    if (!(error instanceof ApiError) || error.statusCode !== 401) {
      throw error;
    }
    if (readSessionCookie() === attemptedCookie) {
      clearSessionCookie();
    }
    await ensureBackendSession();
    return request<T>(path, options);
  }
}
