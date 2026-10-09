import type { MemoirIdentity } from "../domain/memoir";
import {
  ApiError,
  clearSessionCookie,
  readSessionCookie,
  request,
  type RequestOptions,
} from "./request";
import { writeMemoirIdentitySnapshot } from "../stores/memoir-cache";
import { setMemoirCacheIdentity } from "../stores/session";

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

const WECHAT_PLACEHOLDER_DISPLAY_NAME = "微信用户";

export function hasUsableDisplayName(user: BackendAuthUser): boolean {
  const name = user.display_name.trim();
  return Boolean(name && name !== WECHAT_PLACEHOLDER_DISPLAY_NAME);
}

export function needsWeChatProfileSetup(user: BackendAuthUser): boolean {
  return !hasUsableDisplayName(user) || !String(user.avatar_url || "").trim();
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
  const response = await request<AuthResponse>("/api/auth/wechat-login", {
    method: "POST",
    data: { code },
  });
  setMemoirCacheIdentity({ user_id: response.user.id });
  return response;
}

export async function logoutBackendSession(): Promise<void> {
  try {
    await request<unknown>("/api/auth/logout", {
      method: "POST",
      data: {},
    });
  } catch (error) {
    if (!(error instanceof ApiError) || error.statusCode !== 401) {
      throw error;
    }
  }
  clearSessionCookie();
}

export async function deleteAccountWithWeChat(): Promise<void> {
  const code = await wxLoginCode();
  await request<unknown>("/api/auth/me", {
    method: "DELETE",
    data: { code, miniprogram: "laoyu" },
    timeoutProfile: "mutation",
  });
  clearSessionCookie();
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
