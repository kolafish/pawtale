export type HttpMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

const API_BASE_URL = "http://127.0.0.1:8000";
const COOKIE_STORAGE_KEY = "pawtale_miniprogram_cookie";

export class ApiError extends Error {
  constructor(
    readonly statusCode: number,
    message: string,
    readonly data: unknown,
  ) {
    super(message);
  }
}

function cookiePairs(lines: string[]): string {
  return lines
    .map((line) => line.split(";")[0]?.trim())
    .filter((line): line is string => Boolean(line))
    .join("; ");
}

function extractCookies(result: WechatMiniprogram.RequestSuccessCallbackResult): string {
  if (result.cookies?.length) {
    return cookiePairs(result.cookies);
  }
  const raw = result.header["Set-Cookie"] || result.header["set-cookie"];
  if (Array.isArray(raw)) {
    return cookiePairs(raw);
  }
  return raw ? cookiePairs([raw]) : "";
}

function apiMessage(data: unknown): string {
  if (data && typeof data === "object" && "detail" in data) {
    const detail = (data as { detail?: unknown }).detail;
    return typeof detail === "string" ? detail : "请求失败";
  }
  return "请求失败";
}

export function clearSessionCookie(): void {
  wx.removeStorageSync(COOKIE_STORAGE_KEY);
}

export function request<T>(
  path: string,
  options: {
    method?: HttpMethod;
    data?: unknown;
    header?: Record<string, string>;
  } = {},
): Promise<T> {
  const cookie = wx.getStorageSync<string>(COOKIE_STORAGE_KEY);
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.header || {}),
  };
  if (cookie) {
    headers.Cookie = cookie;
  }

  return new Promise<T>((resolve, reject) => {
    wx.request<T>({
      url: `${API_BASE_URL}${path}`,
      method: options.method || "GET",
      data: options.data,
      header: headers,
      timeout: 120000,
      success: (result) => {
        const responseCookie = extractCookies(result);
        if (responseCookie) {
          wx.setStorageSync(COOKIE_STORAGE_KEY, responseCookie);
        }
        if (result.statusCode >= 200 && result.statusCode < 300) {
          resolve(result.data);
          return;
        }
        reject(new ApiError(result.statusCode, apiMessage(result.data), result.data));
      },
      fail: (error) => {
        reject(new ApiError(0, error.errMsg || "网络请求失败", error));
      },
    });
  });
}
