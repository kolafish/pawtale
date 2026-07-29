import { BACKEND_HTTP_BASE_URL } from "../config";

export type HttpMethod = "GET" | "POST" | "PUT" | "DELETE";
export type TimeoutProfile = "telemetry" | "interactive" | "mutation" | "generation";

const COOKIE_KEY = "xiaoyu_backend_cookie";
const TIMEOUT_MS: Record<TimeoutProfile, number> = {
  telemetry: 8000,
  interactive: 30000,
  mutation: 60000,
  generation: 600000,
};

interface RequestResult<T> {
  statusCode: number;
  data: T;
  cookies?: string[];
  header?: Record<string, string | string[] | undefined>;
}

interface RequestFailure {
  errMsg?: string;
}

export interface RequestOptions {
  method?: HttpMethod;
  data?: unknown;
  timeoutProfile?: TimeoutProfile;
}

export class ApiError extends Error {
  constructor(
    readonly statusCode: number,
    message: string,
    readonly data: unknown,
  ) {
    super(message);
  }
}

export function readSessionCookie(): string {
  const value = wx.getStorageSync(COOKIE_KEY);
  return typeof value === "string" ? value : "";
}

export function clearSessionCookie(): void {
  wx.removeStorageSync(COOKIE_KEY);
}

function responseCookie(result: RequestResult<unknown>): string {
  const setCookie = result.header?.["Set-Cookie"] || result.header?.["set-cookie"];
  const values = [
    ...(result.cookies || []),
    ...(Array.isArray(setCookie) ? setCookie : setCookie ? [setCookie] : []),
  ];
  return values
    .map((item) => item.split(";")[0]?.trim() || "")
    .filter(Boolean)
    .join("; ");
}

function apiMessage(data: unknown): string {
  if (data && typeof data === "object" && "detail" in data) {
    const detail = (data as { detail?: unknown }).detail;
    if (typeof detail === "string") {
      return detail;
    }
  }
  return "请求失败";
}

function networkMessage(error: RequestFailure, method: HttpMethod): string {
  const message = String(error.errMsg || "").toLowerCase();
  if (message.includes("url not in domain list")) {
    return "后端请求域名尚未加入微信合法域名";
  }
  if (message.includes("timeout") || message.includes("time out") || message.includes("超时")) {
    return method === "GET"
      ? "请求超时，请检查网络后重试"
      : "请求超时，请刷新确认保存结果后再继续";
  }
  return "网络连接失败，请检查网络后重试";
}

export function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  return new Promise((resolve, reject) => {
    const method = options.method || "GET";
    const cookie = readSessionCookie();
    wx.request({
      url: `${BACKEND_HTTP_BASE_URL.replace(/\/$/, "")}${path}`,
      method,
      data: options.data ?? {},
      timeout: TIMEOUT_MS[options.timeoutProfile || "interactive"],
      header: {
        "content-type": "application/json",
        ...(cookie ? { Cookie: cookie } : {}),
      },
      success(result: RequestResult<T>) {
        const nextCookie = responseCookie(result);
        if (nextCookie) {
          wx.setStorageSync(COOKIE_KEY, nextCookie);
        }
        if (result.statusCode >= 200 && result.statusCode < 300) {
          resolve(result.data);
          return;
        }
        reject(new ApiError(result.statusCode, apiMessage(result.data), result.data));
      },
      fail(error: RequestFailure) {
        reject(new ApiError(0, networkMessage(error, method), error));
      },
    });
  });
}
