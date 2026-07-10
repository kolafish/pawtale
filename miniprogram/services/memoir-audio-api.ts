import { downloadAuthenticatedFile } from "./authenticated-media";
import {
  ApiError,
  cleanBaseUrl,
  readBackendConfig,
} from "./request";

export function memoirAudioPlaybackUrl(audioFileId: string): string {
  const config = readBackendConfig();
  if (!config.httpBaseUrl) {
    throw new ApiError(0, "未配置后端 HTTP 地址", {});
  }
  return `${cleanBaseUrl(config.httpBaseUrl)}/api/mobile/memoir/audio-files/${encodeURIComponent(audioFileId)}/playback`;
}

export function downloadMemoirVoiceAudio(audioFileId: string): Promise<string> {
  const url = memoirAudioPlaybackUrl(audioFileId);
  return downloadAuthenticatedFile(url, {
    failureMessage: "音频下载失败",
    domainLabel: "音频下载",
  });
}
