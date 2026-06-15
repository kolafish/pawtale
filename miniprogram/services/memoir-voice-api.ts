import type {
  MemoirVoiceSessionCreate,
  MemoirVoiceSessionDetail,
} from "../domain/voice-chat";
import { ensureBackendSession } from "./auth";
import {
  ApiError,
  cleanBaseUrl,
  cookieHeaderFromSetCookie,
  readBackendConfig,
  readSessionCookie,
  request,
  writeBackendConfig,
  type BackendRuntimeConfig,
  type BackendRuntimeConfigInput,
} from "./request";

export type VoiceBackendRuntimeConfig = BackendRuntimeConfig;
export type VoiceBackendRuntimeConfigInput = BackendRuntimeConfigInput;
export { cookieHeaderFromSetCookie };

export function readVoiceBackendConfig(): VoiceBackendRuntimeConfig {
  return readBackendConfig();
}

export function writeVoiceBackendConfig(config: VoiceBackendRuntimeConfigInput): void {
  writeBackendConfig(config);
}

export function voiceGatewayUrl(voiceSessionId: string): string {
  const config = readBackendConfig();
  if (!config.wsBaseUrl) {
    throw new ApiError(0, "未配置后端 WebSocket 地址", {});
  }
  return `${cleanBaseUrl(config.wsBaseUrl)}/api/mobile/memoir/voice-sessions/${encodeURIComponent(voiceSessionId)}/gateway`;
}

export function readVoiceBackendCookie(): string {
  return readSessionCookie();
}

export async function voiceBackendCookieHeader(): Promise<string> {
  await ensureBackendSession();
  return readSessionCookie();
}

export async function createMemoirVoiceSession(
  data: MemoirVoiceSessionCreate,
): Promise<MemoirVoiceSessionDetail> {
  await ensureBackendSession();
  return request<MemoirVoiceSessionDetail>("/api/mobile/memoir/voice-sessions", {
    method: "POST",
    data,
  });
}

export async function getMemoirVoiceSession(
  voiceSessionId: string,
): Promise<MemoirVoiceSessionDetail> {
  await ensureBackendSession();
  return request<MemoirVoiceSessionDetail>(
    `/api/mobile/memoir/voice-sessions/${encodeURIComponent(voiceSessionId)}`,
  );
}
