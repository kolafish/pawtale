import { ensureMemoirBackendSession } from "./memoir-api-client";
import { ApiError, readSessionCookie } from "./request";

interface AuthenticatedDownloadOptions {
  failureMessage: string;
  domainLabel: string;
  timeoutMs?: number;
}

interface AuthenticatedUploadOptions<T> {
  filePath: string;
  name: string;
  failureMessage: string;
  domainLabel: string;
  parseResponse: (data: string) => T;
  responseErrorMessage?: (data: string) => string;
  formData?: Record<string, string>;
  timeoutMs?: number;
}

function transferDomain(url: string): string {
  const parts = url.split("/");
  return parts.length >= 3 && parts[0].startsWith("http")
    ? parts.slice(0, 3).join("/")
    : url;
}

function transferErrorMessage(
  errMsg: string | undefined,
  url: string,
  domainLabel: string,
  apiName: "downloadFile" | "uploadFile",
  fallback: string,
): string {
  const message = String(errMsg || "");
  if (message.toLowerCase().includes("url not in domain list")) {
    return `${domainLabel}域名未配置，请在微信公众平台把 ${transferDomain(url)} 添加到 ${apiName} 合法域名。`;
  }
  return message || fallback;
}

function sessionHeader(): Record<string, string> {
  const cookie = readSessionCookie();
  return cookie ? { Cookie: cookie } : {};
}

export async function downloadAuthenticatedFile(
  url: string,
  options: AuthenticatedDownloadOptions,
): Promise<string> {
  await ensureMemoirBackendSession();
  return new Promise((resolve, reject) => {
    wx.downloadFile({
      url,
      ...(options.timeoutMs ? { timeout: options.timeoutMs } : {}),
      header: sessionHeader(),
      success: (result) => {
        if (result.statusCode >= 200 && result.statusCode < 300) {
          resolve(result.tempFilePath);
          return;
        }
        reject(new ApiError(result.statusCode, options.failureMessage, result));
      },
      fail: (error) => {
        reject(new ApiError(
          0,
          transferErrorMessage(
            error.errMsg,
            url,
            options.domainLabel,
            "downloadFile",
            options.failureMessage,
          ),
          error,
        ));
      },
    });
  });
}

export async function uploadAuthenticatedFile<T>(
  url: string,
  options: AuthenticatedUploadOptions<T>,
): Promise<T> {
  await ensureMemoirBackendSession();
  return new Promise((resolve, reject) => {
    wx.uploadFile({
      url,
      filePath: options.filePath,
      name: options.name,
      ...(options.timeoutMs ? { timeout: options.timeoutMs } : {}),
      ...(options.formData ? { formData: options.formData } : {}),
      header: sessionHeader(),
      success: (result) => {
        if (result.statusCode >= 200 && result.statusCode < 300) {
          try {
            resolve(options.parseResponse(result.data));
          } catch (error) {
            reject(error);
          }
          return;
        }
        reject(new ApiError(
          result.statusCode,
          options.responseErrorMessage?.(result.data) || options.failureMessage,
          result.data,
        ));
      },
      fail: (error) => {
        reject(new ApiError(
          0,
          transferErrorMessage(
            error.errMsg,
            url,
            options.domainLabel,
            "uploadFile",
            options.failureMessage,
          ),
          error,
        ));
      },
    });
  });
}
