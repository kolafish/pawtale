import { CLOUD_ENV_ID, CLOUD_FUNCTION_NAME } from "../config";

export type HttpMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

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

function apiMessage(data: unknown): string {
  if (data && typeof data === "object" && "detail" in data) {
    const detail = (data as { detail?: unknown }).detail;
    return typeof detail === "string" ? detail : "请求失败";
  }
  return "请求失败";
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

export function request<T>(
  path: string,
  options: {
    method?: HttpMethod;
    data?: unknown;
  } = {},
): Promise<T> {
  return cloudFunctionRequest<T>(path, options);
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
        data: options.data ?? {},
      },
    });
    const response = result.result;
    if (response.statusCode >= 200 && response.statusCode < 300) {
      return response.data;
    }
    throw new ApiError(response.statusCode, apiMessage(response.data), response.data);
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
    formData?: Record<string, string>;
  },
): Promise<T> {
  return uploadCloudAudioFile<T>(path, options);
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
