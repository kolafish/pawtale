import { ensureMemoirBackendSession } from "./memoir-api-client";
import {
  ApiError,
  cleanBaseUrl,
  readBackendConfig,
  readSessionCookie,
} from "./request";

export function memoirAudioPlaybackUrl(audioFileId: string): string {
  const config = readBackendConfig();
  if (!config.httpBaseUrl) {
    throw new ApiError(0, "未配置后端 HTTP 地址", {});
  }
  return `${cleanBaseUrl(config.httpBaseUrl)}/api/mobile/memoir/audio-files/${encodeURIComponent(audioFileId)}/playback`;
}

function audioDownloadDomain(url: string): string {
  const parts = url.split("/");
  if (parts.length >= 3 && parts[0].startsWith("http")) {
    return parts.slice(0, 3).join("/");
  }
  return url;
}

function audioDownloadErrorMessage(errMsg: string | undefined, url: string): string {
  const message = String(errMsg || "");
  if (message.toLowerCase().includes("url not in domain list")) {
    return `音频下载域名未配置，请在微信公众平台把 ${audioDownloadDomain(url)} 添加到 downloadFile 合法域名。`;
  }
  return message || "音频下载失败";
}

export async function downloadMemoirVoiceAudio(audioFileId: string): Promise<string> {
  await ensureMemoirBackendSession();
  const cookie = readSessionCookie();
  const url = memoirAudioPlaybackUrl(audioFileId);
  return new Promise((resolve, reject) => {
    wx.downloadFile({
      url,
      header: {
        ...(cookie ? { Cookie: cookie } : {}),
      },
      success: (result) => {
        if (result.statusCode >= 200 && result.statusCode < 300) {
          resolve(result.tempFilePath);
          return;
        }
        reject(new ApiError(result.statusCode, "音频下载失败", result));
      },
      fail: (error) => {
        reject(new ApiError(0, audioDownloadErrorMessage(error.errMsg, url), error));
      },
    });
  });
}
