export type HttpMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

export const API_BASE_URL = "http://127.0.0.1:8000";
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

function extractCookies(result: WechatMiniprogram.RequestSuccessCallbackResult | WechatMiniprogram.UploadFileSuccessCallbackResult): string {
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

function requestHeaders(contentType?: string): Record<string, string> {
  const cookie = wx.getStorageSync<string>(COOKIE_STORAGE_KEY);
  const headers: Record<string, string> = {};
  if (contentType) {
    headers["Content-Type"] = contentType;
  }
  if (cookie) {
    headers.Cookie = cookie;
  }
  return headers;
}

function persistCookies(result: WechatMiniprogram.RequestSuccessCallbackResult | WechatMiniprogram.UploadFileSuccessCallbackResult): void {
  const responseCookie = extractCookies(result);
  if (responseCookie) {
    wx.setStorageSync(COOKIE_STORAGE_KEY, responseCookie);
  }
}

function parseJson(data: string): unknown {
  try {
    return JSON.parse(data);
  } catch {
    return data;
  }
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
  const headers: Record<string, string> = {
    ...requestHeaders("application/json"),
    ...(options.header || {}),
  };

  return new Promise<T>((resolve, reject) => {
    wx.request<T>({
      url: `${API_BASE_URL}${path}`,
      method: options.method || "GET",
      data: options.data,
      header: headers,
      timeout: 120000,
      success: (result) => {
        persistCookies(result);
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

export function uploadFile<T>(
  path: string,
  options: {
    filePath: string;
    name: string;
    formData?: Record<string, string>;
  },
): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    wx.uploadFile({
      url: `${API_BASE_URL}${path}`,
      filePath: options.filePath,
      name: options.name,
      formData: options.formData,
      header: requestHeaders(),
      timeout: 120000,
      success: (result) => {
        persistCookies(result);
        const data = parseJson(result.data);
        if (result.statusCode >= 200 && result.statusCode < 300) {
          resolve(data as T);
          return;
        }
        reject(new ApiError(result.statusCode, apiMessage(data), data));
      },
      fail: (error) => {
        reject(new ApiError(0, error.errMsg || "文件上传失败", error));
      },
    });
  });
}
