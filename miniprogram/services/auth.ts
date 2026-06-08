import type { MemoirIdentity, User } from "../domain/memoir";
import { API_TRANSPORT, CLOUD_ENV_ID } from "../config";
import { ApiError, request } from "./request";
import { cacheIdentityFromUser, ensureMemoirStoreIdentity, writeMemoirStore } from "../stores/session";

interface AuthResponse {
  user: User;
}

export interface WechatProfilePatch {
  display_name?: string;
  wechat_nickname?: string;
  wechat_avatar_url?: string;
}

const CLOUD_AVATAR_ROOT = "memoir/avatars";
const WECHAT_LOGIN_TIMEOUT_MS = 60000;

const DEV_USER = {
  email: "miniprogram-dev@example.com",
  password: "password123",
  display_name: "小程序测试用户",
};

function rememberSessionUser(user: User): User {
  ensureMemoirStoreIdentity(cacheIdentityFromUser(user));
  return user;
}

function rememberMemoirIdentity(identity: MemoirIdentity): MemoirIdentity {
  ensureMemoirStoreIdentity(cacheIdentityFromUser(identity, identity.active_project));
  writeMemoirStore({ project: identity.active_project });
  return identity;
}

function runWechatLogin(): Promise<void> {
  return new Promise((resolve, reject) => {
    wx.login({
      timeout: WECHAT_LOGIN_TIMEOUT_MS,
      success: (result) => {
        if (result.code) {
          resolve();
          return;
        }
        reject(new ApiError(0, result.errMsg || "微信登录失败", result));
      },
      fail: (error) => {
        reject(new ApiError(0, error.errMsg || "微信登录失败", error));
      },
    });
  });
}

let wechatLoginPromise: Promise<void> | null = null;

function ensureWechatLogin(): Promise<void> {
  if (API_TRANSPORT !== "cloudFunction") {
    return Promise.resolve();
  }
  if (!wechatLoginPromise) {
    wechatLoginPromise = runWechatLogin().catch((error) => {
      wechatLoginPromise = null;
      throw error;
    });
  }
  return wechatLoginPromise;
}

export async function getMemoirIdentity(): Promise<MemoirIdentity> {
  await ensureWechatLogin();
  return rememberMemoirIdentity(await request<MemoirIdentity>("/api/mobile/memoir/me"));
}

export async function loginWithWechat(): Promise<MemoirIdentity> {
  if (API_TRANSPORT === "cloudFunction") {
    wechatLoginPromise = runWechatLogin().catch((error) => {
      wechatLoginPromise = null;
      throw error;
    });
    await wechatLoginPromise;
  }
  return rememberMemoirIdentity(await request<MemoirIdentity>("/api/mobile/memoir/me"));
}

function safePathSegment(value: string): string {
  return value.replace(/[^a-zA-Z0-9_-]/g, "_");
}

function fileExtension(filePath: string): string {
  const cleanPath = filePath.split("?")[0] || "";
  const match = /\.([a-zA-Z0-9]+)$/.exec(cleanPath);
  return match ? match[1].toLowerCase() : "jpg";
}

export async function uploadWechatAvatar(filePath: string, ownerId: string): Promise<string> {
  if (/^(cloud|https?):\/\//.test(filePath)) {
    return filePath;
  }
  const extension = fileExtension(filePath);
  const random = Math.random().toString(36).slice(2, 10);
  const cloudPath = `${CLOUD_AVATAR_ROOT}/${safePathSegment(ownerId)}/${Date.now()}-${random}.${extension}`;
  const result = await wx.cloud.uploadFile({
    config: {
      env: CLOUD_ENV_ID,
    },
    cloudPath,
    filePath,
  });
  return result.fileID;
}

export async function updateWechatUserProfile(data: WechatProfilePatch): Promise<MemoirIdentity> {
  await request<User>("/api/mobile/memoir/me", {
    method: "PATCH",
    data,
  });
  return getMemoirIdentity();
}

export async function ensureLocalDevSession(): Promise<User> {
  if (API_TRANSPORT === "cloudFunction") {
    return getMemoirIdentity();
  }

  try {
    return rememberSessionUser(await request<User>("/api/auth/me"));
  } catch (error) {
    if (!(error instanceof ApiError) || error.statusCode !== 401) {
      throw error;
    }
  }

  try {
    const registered = await request<AuthResponse>("/api/auth/register", {
      method: "POST",
      data: DEV_USER,
    });
    return rememberSessionUser(registered.user);
  } catch (error) {
    if (!(error instanceof ApiError) || error.statusCode !== 409) {
      throw error;
    }
  }

  const loggedIn = await request<AuthResponse>("/api/auth/login", {
    method: "POST",
    data: {
      email: DEV_USER.email,
      password: DEV_USER.password,
    },
  });
  return rememberSessionUser(loggedIn.user);
}
