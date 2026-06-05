import { API_TRANSPORT, CLOUD_ENV_ID, CLOUD_FUNCTION_NAME, LOCAL_API_BASE_URL } from "../config";

export type HttpMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

export const API_BASE_URL = LOCAL_API_BASE_URL;
const COOKIE_STORAGE_KEY = "pawtale_miniprogram_cookie";
const CLOUD_AUDIO_ROOT = "memoir/audio";

interface CloudApiResponse<T> {
  statusCode: number;
  data: T;
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

function cloudApiMessage(data: unknown): string {
  return apiMessage(data);
}

function fileExtension(filePath: string): string {
  const cleanPath = filePath.split("?")[0] || "";
  const match = /\.([a-zA-Z0-9]+)$/.exec(cleanPath);
  return match ? match[1].toLowerCase() : "mp3";
}

function audioMimeType(extension: string): string {
  if (extension === "wav") {
    return "audio/wav";
  }
  if (extension === "ogg") {
    return "audio/ogg";
  }
  if (extension === "pcm") {
    return "audio/pcm";
  }
  return "audio/mpeg";
}

interface CloudUser {
  id: string;
  storage_owner_id?: string;
}

function safePathSegment(value: string): string {
  return value.replace(/[^a-zA-Z0-9_-]/g, "_");
}

function uniqueCloudAudioPath(filePath: string, ownerId: string): string {
  const extension = fileExtension(filePath);
  const random = Math.random().toString(36).slice(2, 10);
  return `${CLOUD_AUDIO_ROOT}/${safePathSegment(ownerId)}/${Date.now()}-${random}.${extension}`;
}

function localFileSize(filePath: string): Promise<number> {
  return new Promise((resolve, reject) => {
    wx.getFileInfo({
      filePath,
      success: (result) => {
        resolve(result.size);
      },
      fail: (error) => {
        reject(new ApiError(0, error.errMsg || "音频文件信息读取失败", error));
      },
    });
  });
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
  if (API_TRANSPORT === "cloudFunction") {
    return cloudFunctionRequest<T>(path, options);
  }

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

async function cloudFunctionRequest<T>(
  path: string,
  options: {
    method?: HttpMethod;
    data?: unknown;
  } = {},
): Promise<T> {
  try {
    const result = await wx.cloud.callFunction<CloudApiResponse<T>>({
      name: CLOUD_FUNCTION_NAME,
      config: {
        env: CLOUD_ENV_ID,
      },
      data: {
        path,
        method: options.method || "GET",
        data: options.data || {},
      },
    });
    const response = result.result;
    if (response.statusCode >= 200 && response.statusCode < 300) {
      return response.data;
    }
    throw new ApiError(response.statusCode, cloudApiMessage(response.data), response.data);
  } catch (error) {
    if (error instanceof ApiError) {
      throw error;
    }
    const message = error instanceof Error ? error.message : "云函数请求失败";
    throw new ApiError(0, message, error);
  }
}

export function uploadFile<T>(
  path: string,
  options: {
    filePath: string;
    name: string;
    formData?: Record<string, string>;
  },
): Promise<T> {
  if (API_TRANSPORT === "cloudFunction") {
    return uploadCloudAudioFile<T>(path, options);
  }

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

async function uploadCloudAudioFile<T>(
  path: string,
  options: {
    filePath: string;
    formData?: Record<string, string>;
  },
): Promise<T> {
  const cloudUser = await cloudFunctionRequest<CloudUser>("/api/mobile/memoir/me");
  const ownerId = cloudUser.storage_owner_id || cloudUser.id;
  const cloudPath = uniqueCloudAudioPath(options.filePath, ownerId);
  const extension = fileExtension(options.filePath);
  const durationSeconds = options.formData?.duration_seconds
    ? Number(options.formData.duration_seconds)
    : undefined;
  const sizeBytes = options.formData?.size_bytes
    ? Number(options.formData.size_bytes)
    : await localFileSize(options.filePath);

  try {
    const uploadResult = await wx.cloud.uploadFile({
      config: {
        env: CLOUD_ENV_ID,
      },
      cloudPath,
      filePath: options.filePath,
    });

    return cloudFunctionRequest<T>(path, {
      method: "POST",
      data: {
        file_id: uploadResult.fileID,
        cloud_path: cloudPath,
        mime_type: audioMimeType(extension),
        size_bytes: Number.isFinite(sizeBytes) ? sizeBytes : undefined,
        duration_seconds: Number.isFinite(durationSeconds) ? durationSeconds : undefined,
      },
    });
  } catch (error) {
    if (error instanceof ApiError) {
      throw error;
    }
    const message = error instanceof Error ? error.message : "云存储上传失败";
    throw new ApiError(0, message, error);
  }
}
