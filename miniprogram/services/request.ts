import {
  BACKEND_DEV_DISPLAY_NAME,
  BACKEND_DEV_EMAIL,
  BACKEND_DEV_PASSWORD,
  BACKEND_HTTP_BASE_URL,
  BACKEND_WS_BASE_URL,
} from "../config";

export type HttpMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

const BACKEND_COOKIE_KEY = "pawtale_backend_cookie";
const BACKEND_CONFIG_STORAGE_KEY = "pawtale_backend_config";
const REQUEST_TIMEOUT_MS = 10000;

export interface BackendRuntimeConfig {
  httpBaseUrl: string;
  wsBaseUrl: string;
  devEmail: string;
  devPassword: string;
  devDisplayName: string;
}

export interface BackendRuntimeConfigInput {
  httpBaseUrl?: string;
  wsBaseUrl?: string;
  devEmail?: string;
  devPassword?: string;
  devDisplayName?: string;
}

interface RequestOptions {
  method?: HttpMethod;
  data?: unknown;
  header?: Record<string, string>;
  timeoutMs?: number;
}

type WxWithExtConfig = typeof wx & {
  getExtConfigSync?: () => Record<string, unknown>;
};

export class ApiError extends Error {
  constructor(
    readonly statusCode: number,
    message: string,
    readonly data: unknown,
  ) {
    super(message);
  }
}

export function cleanBaseUrl(baseUrl: string): string {
  return baseUrl.replace(/\/$/, "");
}

function stringFromConfig(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function storageConfigValue(key: string): BackendRuntimeConfigInput {
  const stored = wx.getStorageSync<BackendRuntimeConfigInput | string>(key);
  if (!stored || typeof stored === "string") {
    return {};
  }
  return stored;
}

function storageConfig(): BackendRuntimeConfigInput {
  return storageConfigValue(BACKEND_CONFIG_STORAGE_KEY);
}

function extConfig(): BackendRuntimeConfigInput {
  const wxWithExtConfig = wx as WxWithExtConfig;
  if (typeof wxWithExtConfig.getExtConfigSync !== "function") {
    return {};
  }
  const ext = wxWithExtConfig.getExtConfigSync();
  return {
    httpBaseUrl: stringFromConfig(ext.backendHttpBaseUrl),
    wsBaseUrl: stringFromConfig(ext.backendWsBaseUrl),
    devEmail: stringFromConfig(ext.backendDevEmail),
    devPassword: stringFromConfig(ext.backendDevPassword),
    devDisplayName: stringFromConfig(ext.backendDevDisplayName),
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

export function readBackendConfig(): BackendRuntimeConfig {
  const stored = storageConfig();
  const ext = extConfig();
  const configuredHttpBaseUrl =
    stringFromConfig(stored.httpBaseUrl) ||
    stringFromConfig(ext.httpBaseUrl);
  const defaultHttpBaseUrl = stringFromConfig(BACKEND_HTTP_BASE_URL);
  const httpBaseUrl = configuredHttpBaseUrl || defaultHttpBaseUrl;
  const explicitWsBaseUrl =
    stringFromConfig(stored.wsBaseUrl) ||
    stringFromConfig(ext.wsBaseUrl);
  const defaultWsBaseUrl = stringFromConfig(BACKEND_WS_BASE_URL);
  const wsBaseUrl =
    explicitWsBaseUrl ||
    (configuredHttpBaseUrl ? deriveWsBaseUrl(configuredHttpBaseUrl) : "") ||
    defaultWsBaseUrl ||
    deriveWsBaseUrl(httpBaseUrl);
  return {
    httpBaseUrl,
    wsBaseUrl,
    devEmail:
      stringFromConfig(stored.devEmail) ||
      stringFromConfig(ext.devEmail) ||
      stringFromConfig(BACKEND_DEV_EMAIL),
    devPassword:
      stringFromConfig(stored.devPassword) ||
      stringFromConfig(ext.devPassword) ||
      stringFromConfig(BACKEND_DEV_PASSWORD),
    devDisplayName:
      stringFromConfig(stored.devDisplayName) ||
      stringFromConfig(ext.devDisplayName) ||
      stringFromConfig(BACKEND_DEV_DISPLAY_NAME),
  };
}

export function writeBackendConfig(config: BackendRuntimeConfigInput): void {
  wx.setStorageSync(BACKEND_CONFIG_STORAGE_KEY, config);
}

export function readSessionCookie(): string {
  return wx.getStorageSync<string>(BACKEND_COOKIE_KEY) || "";
}

export function clearSessionCookie(): void {
  wx.removeStorageSync(BACKEND_COOKIE_KEY);
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

function writeSessionCookie(
  cookies: string[] | undefined,
  header: Record<string, string | string[] | undefined>,
): void {
  const nextCookie = cookieHeaderFromSetCookie(cookies, header);
  if (nextCookie) {
    wx.setStorageSync(BACKEND_COOKIE_KEY, nextCookie);
  }
}

function apiMessage(data: unknown): string {
  if (data && typeof data === "object" && "detail" in data) {
    const detail = (data as { detail?: unknown }).detail;
    return typeof detail === "string" ? detail : "请求失败";
  }
  return "请求失败";
}

export function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  return new Promise((resolve, reject) => {
    const config = readBackendConfig();
    if (!config.httpBaseUrl) {
      reject(new ApiError(0, "未配置后端 HTTP 地址", {}));
      return;
    }
    const cookie = readSessionCookie();
    wx.request<T>({
      url: `${cleanBaseUrl(config.httpBaseUrl)}${path}`,
      method: options.method || "GET",
      data: options.data ?? {},
      timeout: options.timeoutMs ?? REQUEST_TIMEOUT_MS,
      header: {
        "content-type": "application/json",
        ...(cookie ? { Cookie: cookie } : {}),
        ...(options.header || {}),
      },
      success: (result) => {
        writeSessionCookie(
          result.cookies,
          result.header as Record<string, string | string[] | undefined>,
        );
        if (result.statusCode >= 200 && result.statusCode < 300) {
          resolve(result.data);
          return;
        }
        reject(new ApiError(result.statusCode, apiMessage(result.data), result.data));
      },
      fail: (error) => {
        reject(new ApiError(0, error.errMsg || "后端请求失败", error));
      },
    });
  });
}
