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
const VOICE_BACKEND_CONFIG_STORAGE_KEY = "pawtale_voice_backend_config";

interface VoiceBackendRequestOptions {
  method?: WechatMiniprogram.RequestMethod;
  data?: unknown;
}

export interface VoiceBackendRuntimeConfig {
  httpBaseUrl: string;
  wsBaseUrl: string;
  devEmail: string;
  devPassword: string;
  devDisplayName: string;
}

interface VoiceBackendRuntimeConfigInput {
  httpBaseUrl?: string;
  wsBaseUrl?: string;
  devEmail?: string;
  devPassword?: string;
  devDisplayName?: string;
}

type WxWithExtConfig = typeof wx & {
  getExtConfigSync?: () => Record<string, unknown>;
};

function cleanBaseUrl(baseUrl: string): string {
  return baseUrl.replace(/\/$/, "");
}

function stringFromConfig(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function storageConfig(): VoiceBackendRuntimeConfigInput {
  const stored = wx.getStorageSync<VoiceBackendRuntimeConfigInput | string>(VOICE_BACKEND_CONFIG_STORAGE_KEY);
  if (!stored || typeof stored === "string") {
    return {};
  }
  return stored;
}

function extConfig(): VoiceBackendRuntimeConfigInput {
  const wxWithExtConfig = wx as WxWithExtConfig;
  if (typeof wxWithExtConfig.getExtConfigSync !== "function") {
    return {};
  }
  const ext = wxWithExtConfig.getExtConfigSync();
  return {
    httpBaseUrl: stringFromConfig(ext.voiceBackendHttpBaseUrl),
    wsBaseUrl: stringFromConfig(ext.voiceBackendWsBaseUrl),
    devEmail: stringFromConfig(ext.voiceBackendDevEmail),
    devPassword: stringFromConfig(ext.voiceBackendDevPassword),
    devDisplayName: stringFromConfig(ext.voiceBackendDevDisplayName),
  };
}

function deriveWsBaseUrl(httpBaseUrl: string): string {
  if (!httpBaseUrl) {
    return "";
  }
  if (httpBaseUrl.startsWith("https://")) {
    return `wss://${httpBaseUrl.slice("https://".length)}`;
  }
  if (httpBaseUrl.startsWith("http://")) {
    return `ws://${httpBaseUrl.slice("http://".length)}`;
  }
  return "";
}

export function readVoiceBackendConfig(): VoiceBackendRuntimeConfig {
  const stored = storageConfig();
  const ext = extConfig();
  const httpBaseUrl =
    stringFromConfig(stored.httpBaseUrl) ||
    stringFromConfig(ext.httpBaseUrl) ||
    stringFromConfig(VOICE_BACKEND_HTTP_BASE_URL);
  const wsBaseUrl =
    stringFromConfig(stored.wsBaseUrl) ||
    stringFromConfig(ext.wsBaseUrl) ||
    stringFromConfig(VOICE_BACKEND_WS_BASE_URL) ||
    deriveWsBaseUrl(httpBaseUrl);
  return {
    httpBaseUrl,
    wsBaseUrl,
    devEmail:
      stringFromConfig(stored.devEmail) ||
      stringFromConfig(ext.devEmail) ||
      stringFromConfig(VOICE_BACKEND_DEV_EMAIL),
    devPassword:
      stringFromConfig(stored.devPassword) ||
      stringFromConfig(ext.devPassword) ||
      stringFromConfig(VOICE_BACKEND_DEV_PASSWORD),
    devDisplayName:
      stringFromConfig(stored.devDisplayName) ||
      stringFromConfig(ext.devDisplayName) ||
      stringFromConfig(VOICE_BACKEND_DEV_DISPLAY_NAME),
  };
}

export function writeVoiceBackendConfig(config: VoiceBackendRuntimeConfigInput): void {
  wx.setStorageSync(VOICE_BACKEND_CONFIG_STORAGE_KEY, config);
}

export function voiceGatewayUrl(voiceSessionId: string): string {
  const config = readVoiceBackendConfig();
  if (!config.wsBaseUrl) {
    throw new ApiError(0, "未配置语音后端 WebSocket 地址", {});
  }
  return `${cleanBaseUrl(config.wsBaseUrl)}/api/mobile/memoir/voice-sessions/${encodeURIComponent(voiceSessionId)}/gateway`;
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
  const config = readVoiceBackendConfig();
  const currentCookie = readVoiceBackendCookie();
  if (currentCookie) {
    try {
      await voiceBackendRequest<unknown>("/api/auth/me");
      return;
    } catch {
      wx.removeStorageSync(VOICE_BACKEND_COOKIE_KEY);
    }
  }

  if (!config.devEmail || !config.devPassword) {
    throw new ApiError(0, "未配置语音后端开发账号", {});
  }

  try {
    await voiceBackendRequest<unknown>("/api/auth/login", {
      method: "POST",
      data: {
        email: config.devEmail,
        password: config.devPassword,
      },
    });
  } catch (error) {
    if (!(error instanceof ApiError) || ![401, 404].includes(error.statusCode)) {
      throw error;
    }
    await voiceBackendRequest<unknown>("/api/auth/register", {
      method: "POST",
      data: {
        email: config.devEmail,
        password: config.devPassword,
        display_name: config.devDisplayName || "本地作者",
      },
    });
  }
}

function voiceBackendRequest<T>(path: string, options: VoiceBackendRequestOptions = {}): Promise<T> {
  return new Promise((resolve, reject) => {
    const config = readVoiceBackendConfig();
    if (!config.httpBaseUrl) {
      reject(new ApiError(0, "未配置语音后端 HTTP 地址", {}));
      return;
    }
    const cookie = readVoiceBackendCookie();
    wx.request<T>({
      url: `${cleanBaseUrl(config.httpBaseUrl)}${path}`,
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
