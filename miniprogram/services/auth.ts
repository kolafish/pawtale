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

export interface WechatProfilePatch {
  display_name?: string;
  wechat_nickname?: string;
  wechat_avatar_url?: string;
}

export interface BackendAuthUser {
  id: string;
  email: string;
  display_name: string;
  avatar_url?: string | null;
  created_at: string;
}

interface AuthResponse {
  user: BackendAuthUser;
}

export interface LoginCredentials {
  email: string;
  password: string;
}

export interface RegisterCredentials extends LoginCredentials {
  display_name?: string;
}

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

export async function uploadWechatAvatar(filePath: string, _ownerId?: string): Promise<string> {
  if (/^https?:\/\//.test(filePath)) {
    return filePath;
  }
  throw new ApiError(0, "ECS 头像上传接口暂未接入", {});
}

export async function updateWechatUserProfile(_data: WechatProfilePatch): Promise<MemoirIdentity> {
  throw new ApiError(0, "ECS 资料编辑接口暂未接入", {});
}
