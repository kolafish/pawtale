import type { MemoirIdentity } from "../domain/memoir";
import {
  ApiError,
  clearSessionCookie,
  readBackendConfig,
  readSessionCookie,
  request,
} from "./request";
import { writeMemoirIdentitySnapshot } from "../stores/memoir-cache";
import { cacheIdentityFromUser, ensureMemoirStoreIdentity } from "../stores/session";

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

export interface LoginCredentials {
  email: string;
  password: string;
}

export interface RegisterCredentials extends LoginCredentials {
  display_name?: string;
}

export interface SetCredentialsInput extends RegisterCredentials {}

function rememberMemoirIdentity(identity: MemoirIdentity): MemoirIdentity {
  ensureMemoirStoreIdentity(cacheIdentityFromUser(identity, identity.active_project));
  writeMemoirIdentitySnapshot(identity);
  return identity;
}

export function currentSessionCookie(): string {
  return readSessionCookie();
}

export async function loginWithEmail(credentials: LoginCredentials): Promise<BackendAuthUser> {
  const response = await request<AuthResponse>("/api/auth/login", {
    method: "POST",
    data: credentials,
  });
  return response.user;
}

export async function registerWithEmail(credentials: RegisterCredentials): Promise<BackendAuthUser> {
  const response = await request<AuthResponse>("/api/auth/register", {
    method: "POST",
    data: credentials,
  });
  return response.user;
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

export async function setAccountCredentials(credentials: SetCredentialsInput): Promise<AuthResponse> {
  return request<AuthResponse>("/api/auth/set-credentials", {
    method: "POST",
    data: credentials,
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

export async function getBackendUser(): Promise<BackendAuthUser> {
  return request<BackendAuthUser>("/api/auth/me");
}

export async function ensureBackendSession(): Promise<void> {
  if (readSessionCookie()) {
    try {
      await getBackendUser();
      return;
    } catch (error) {
      if (error instanceof ApiError && [401, 403].includes(error.statusCode)) {
        clearSessionCookie();
      } else {
        throw error;
      }
    }
  }

  const config = readBackendConfig();
  if (!config.devEmail || !config.devPassword) {
    throw new ApiError(401, "请先登录", {});
  }

  try {
    await loginWithEmail({
      email: config.devEmail,
      password: config.devPassword,
    });
  } catch (error) {
    if (!(error instanceof ApiError) || ![401, 404].includes(error.statusCode)) {
      throw error;
    }
    await registerWithEmail({
      email: config.devEmail,
      password: config.devPassword,
      display_name: config.devDisplayName || "本地作者",
    });
  }
}

export async function getMemoirIdentity(): Promise<MemoirIdentity> {
  await ensureBackendSession();
  return rememberMemoirIdentity(await request<MemoirIdentity>("/api/mobile/memoir/me"));
}
