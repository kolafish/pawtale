import type { MemoirIdentity } from "../domain/memoir";
import {
  ApiError,
  clearSessionCookie,
  readSessionCookie,
  request,
  type RequestOptions,
} from "./request";
import { writeMemoirIdentitySnapshot } from "../stores/memoir-cache";

export interface BackendAuthUser {
  id: string;
  email: string;
  password_login_enabled: boolean;
  display_name: string;
  avatar_url?: string | null;
  created_at: string;
}

export interface AuthResponse {
  user: BackendAuthUser;
  needs_credentials: boolean;
}

export interface UpdateProfileInput {
  display_name: string;
}

function rememberMemoirIdentity(identity: MemoirIdentity): MemoirIdentity {
  writeMemoirIdentitySnapshot(identity);
  return identity;
}

function wxLoginCode(): Promise<string> {
  return new Promise((resolve, reject) => {
    wx.login({
      success(result) {
        if (result.code) {
          resolve(result.code);
          return;
        }
        reject(new ApiError(0, "微信登录未返回 code", result));
      },
      fail(error) {
        reject(new ApiError(0, error.errMsg || "微信登录失败", error));
      },
    });
  });
}

export async function loginWithWeChat(): Promise<AuthResponse> {
  const code = await wxLoginCode();
  return request<AuthResponse>("/api/auth/wechat-login", {
    method: "POST",
    data: { code },
  });
}

export async function logoutBackendSession(): Promise<void> {
  try {
    await request<unknown>("/api/auth/logout", {
      method: "POST",
      data: {},
    });
  } finally {
    clearSessionCookie();
  }
}

export async function updateBackendProfile(input: UpdateProfileInput): Promise<BackendAuthUser> {
  return request<BackendAuthUser>("/api/auth/me", {
    method: "PATCH",
    data: input,
  });
}

export async function ensureBackendSession(): Promise<void> {
  if (!readSessionCookie()) {
    throw new ApiError(401, "请先登录", {});
  }
}

export async function requestWithBackendSession<T>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  await ensureBackendSession();
  const attemptedSessionCookie = readSessionCookie();
  try {
    return await request<T>(path, options);
  } catch (error) {
    if (!(error instanceof ApiError) || error.statusCode !== 401) {
      throw error;
    }
    if (readSessionCookie() === attemptedSessionCookie) {
      clearSessionCookie();
    }
    throw error;
  }
}

export async function getMemoirIdentity(): Promise<MemoirIdentity> {
  return rememberMemoirIdentity(
    await requestWithBackendSession<MemoirIdentity>("/api/mobile/memoir/me"),
  );
}
