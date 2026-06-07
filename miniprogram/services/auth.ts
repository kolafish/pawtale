import type { MemoirIdentity, User } from "../domain/memoir";
import { API_TRANSPORT } from "../config";
import { ApiError, request } from "./request";
import { cacheIdentityFromUser, ensureMemoirStoreIdentity } from "../stores/session";

interface AuthResponse {
  user: User;
}

export interface WechatProfilePatch {
  display_name?: string;
  wechat_nickname?: string;
  wechat_avatar_url?: string;
}

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
  return identity;
}

export async function getMemoirIdentity(): Promise<MemoirIdentity> {
  return rememberMemoirIdentity(await request<MemoirIdentity>("/api/mobile/memoir/me"));
}

export async function updateWechatUserProfile(data: WechatProfilePatch): Promise<User> {
  const user = await request<User>("/api/mobile/memoir/me", {
    method: "PATCH",
    data,
  });
  ensureMemoirStoreIdentity(cacheIdentityFromUser(user));
  return user;
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
