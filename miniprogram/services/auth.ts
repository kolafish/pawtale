import type { MemoirIdentity } from "../domain/memoir";
import { CLOUD_ENV_ID } from "../config";
import { ApiError, request } from "./request";
import { writeMemoirIdentitySnapshot } from "../stores/memoir-cache";
import { cacheIdentityFromUser, ensureMemoirStoreIdentity } from "../stores/session";

export interface WechatProfilePatch {
  display_name?: string;
  wechat_nickname?: string;
  wechat_avatar_url?: string;
}

const CLOUD_AVATAR_ROOT = "memoir/avatars";
const WECHAT_LOGIN_TIMEOUT_MS = 60000;

function rememberMemoirIdentity(identity: MemoirIdentity): MemoirIdentity {
  ensureMemoirStoreIdentity(cacheIdentityFromUser(identity, identity.active_project));
  writeMemoirIdentitySnapshot(identity);
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
  await request<unknown>("/api/mobile/memoir/me", {
    method: "PATCH",
    data,
  });
  return getMemoirIdentity();
}
