import {
  VOICE_BACKEND_DEV_DISPLAY_NAME,
  VOICE_BACKEND_DEV_EMAIL,
  VOICE_BACKEND_DEV_PASSWORD,
  VOICE_BACKEND_HTTP_BASE_URL,
  VOICE_BACKEND_WS_BASE_URL,
} from "../config";
import type {
  MemoirVoiceSessionCreate,
  MemoirVoiceSessionDetail,
} from "../domain/voice-chat";
import { ApiError } from "./request";

const VOICE_BACKEND_COOKIE_KEY = "pawtale_voice_backend_cookie";

interface VoiceBackendRequestOptions {
  method?: WechatMiniprogram.RequestMethod;
  data?: unknown;
}

function cleanBaseUrl(baseUrl: string): string {
  return baseUrl.replace(/\/$/, "");
}

export function voiceGatewayUrl(voiceSessionId: string): string {
  return `${cleanBaseUrl(VOICE_BACKEND_WS_BASE_URL)}/api/mobile/memoir/voice-sessions/${encodeURIComponent(voiceSessionId)}/gateway`;
}

export function readVoiceBackendCookie(): string {
  return wx.getStorageSync<string>(VOICE_BACKEND_COOKIE_KEY) || "";
}

export function cookieHeaderFromSetCookie(
  cookies: string[] | undefined,
  header: Record<string, string | string[] | undefined>,
): string {
  const setCookie = header["Set-Cookie"] || header["set-cookie"];
  const headerCookies = Array.isArray(setCookie) ? setCookie : setCookie ? [setCookie] : [];
  return [...(cookies || []), ...headerCookies]
    .map((item) => item.split(";")[0]?.trim() || "")
    .filter(Boolean)
    .join("; ");
}

function writeVoiceBackendCookie(cookies: string[] | undefined, header: Record<string, string | string[] | undefined>): void {
  const nextCookie = cookieHeaderFromSetCookie(cookies, header);
  if (nextCookie) {
    wx.setStorageSync(VOICE_BACKEND_COOKIE_KEY, nextCookie);
  }
}

export async function voiceBackendCookieHeader(): Promise<string> {
  await ensureVoiceBackendSession();
  return readVoiceBackendCookie();
}

export async function createMemoirVoiceSession(
  data: MemoirVoiceSessionCreate,
): Promise<MemoirVoiceSessionDetail> {
  await ensureVoiceBackendSession();
  return voiceBackendRequest<MemoirVoiceSessionDetail>("/api/mobile/memoir/voice-sessions", {
    method: "POST",
    data,
  });
}

async function ensureVoiceBackendSession(): Promise<void> {
  const currentCookie = readVoiceBackendCookie();
  if (currentCookie) {
    try {
      await voiceBackendRequest<unknown>("/api/auth/me");
      return;
    } catch {
      wx.removeStorageSync(VOICE_BACKEND_COOKIE_KEY);
    }
  }

  try {
    await voiceBackendRequest<unknown>("/api/auth/login", {
      method: "POST",
      data: {
        email: VOICE_BACKEND_DEV_EMAIL,
        password: VOICE_BACKEND_DEV_PASSWORD,
      },
    });
  } catch (error) {
    if (!(error instanceof ApiError) || ![401, 404].includes(error.statusCode)) {
      throw error;
    }
    await voiceBackendRequest<unknown>("/api/auth/register", {
      method: "POST",
      data: {
        email: VOICE_BACKEND_DEV_EMAIL,
        password: VOICE_BACKEND_DEV_PASSWORD,
        display_name: VOICE_BACKEND_DEV_DISPLAY_NAME,
      },
    });
  }
}

function voiceBackendRequest<T>(path: string, options: VoiceBackendRequestOptions = {}): Promise<T> {
  return new Promise((resolve, reject) => {
    const cookie = readVoiceBackendCookie();
    wx.request<T>({
      url: `${cleanBaseUrl(VOICE_BACKEND_HTTP_BASE_URL)}${path}`,
      method: options.method || "GET",
      data: options.data ?? {},
      header: cookie ? { Cookie: cookie } : {},
      success: (result) => {
        writeVoiceBackendCookie(result.cookies, result.header);
        if (result.statusCode >= 200 && result.statusCode < 300) {
          resolve(result.data);
          return;
        }
        reject(new ApiError(result.statusCode, apiMessage(result.data), result.data));
      },
      fail: (error) => {
        reject(new ApiError(0, error.errMsg || "语音后端请求失败", error));
      },
    });
  });
}

function apiMessage(data: unknown): string {
  if (data && typeof data === "object" && "detail" in data) {
    const detail = (data as { detail?: unknown }).detail;
    return typeof detail === "string" ? detail : "语音后端请求失败";
  }
  return "语音后端请求失败";
}
