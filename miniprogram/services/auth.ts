import type { MemoirIdentity } from "../domain/memoir";
import {
  ApiError,
  clearSessionCookie,
  readBackendConfig,
  readSessionCookie,
  request,
  type RequestOptions,
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

export interface UpdateProfileInput {
  display_name: string;
}

let backendSessionPromise: Promise<void> | null = null;

function rememberMemoirIdentity(identity: MemoirIdentity): MemoirIdentity {
  ensureMemoirStoreIdentity(cacheIdentityFromUser(identity, identity.active_project || undefined));
  writeMemoirIdentitySnapshot(identity);
  return identity;
}

export function isValidEmail(email: string): boolean {
  const normalized = email.trim();
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(normalized);
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

export async function updateBackendProfile(input: UpdateProfileInput): Promise<BackendAuthUser> {
  return request<BackendAuthUser>("/api/auth/me", {
    method: "PATCH",
    data: input,
  });
}

async function establishBackendSession(): Promise<void> {
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

export async function ensureBackendSession(): Promise<void> {
  if (readSessionCookie()) {
    return;
  }
  if (!backendSessionPromise) {
    backendSessionPromise = establishBackendSession().finally(() => {
      backendSessionPromise = null;
    });
  }
  await backendSessionPromise;
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
    await ensureBackendSession();
    return request<T>(path, options);
  }
}

export async function getMemoirIdentity(): Promise<MemoirIdentity> {
  return rememberMemoirIdentity(
    await requestWithBackendSession<MemoirIdentity>("/api/mobile/memoir/me"),
  );
}
