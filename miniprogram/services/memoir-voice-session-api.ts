import type {
  MemoirAssistantTTSRequest,
  MemoirAssistantTTSResponse,
  MemoirAutomationEventListResponse,
  MemoirProjectProgressSnapshot,
  MemoirVoiceFollowUpResponse,
  MemoirVoiceNoAnswerPromptRequest,
  MemoirVoiceProgressResponse,
  MemoirVoiceSessionCreate,
  MemoirVoiceSessionDetail,
  MemoirVoiceSessionListResponse,
} from "../domain/voice-chat";
import {
  ensureMemoirBackendSession,
  memoirRequest,
  memoirVoiceSessionPath,
  queryString,
} from "./memoir-api-client";
import {
  ApiError,
  cleanBaseUrl,
  readBackendConfig,
  readSessionCookie,
} from "./request";

interface VoiceSessionListOptions {
  projectId?: string;
  effectiveOnly?: boolean;
  limit?: number;
  offset?: number;
}

export function voiceGatewayUrl(voiceSessionId: string): string {
  const config = readBackendConfig();
  if (!config.wsBaseUrl) {
    throw new ApiError(0, "未配置后端 WebSocket 地址", {});
  }
  return `${cleanBaseUrl(config.wsBaseUrl)}${memoirVoiceSessionPath(voiceSessionId, "/gateway")}`;
}

export function assistantTurnStreamUrl(voiceSessionId: string): string {
  const config = readBackendConfig();
  if (!config.wsBaseUrl) {
    throw new ApiError(0, "未配置后端 WebSocket 地址", {});
  }
  return `${cleanBaseUrl(config.wsBaseUrl)}${memoirVoiceSessionPath(voiceSessionId, "/assistant-turn-stream")}`;
}

export async function backendSessionCookieHeader(): Promise<string> {
  await ensureMemoirBackendSession();
  return readSessionCookie();
}

export function createMemoirVoiceSession(
  data: MemoirVoiceSessionCreate,
  options: { projectId?: string } = {},
): Promise<MemoirVoiceSessionDetail> {
  return memoirRequest<MemoirVoiceSessionDetail>(
    `/api/mobile/memoir/voice-sessions${queryString({ project_id: options.projectId })}`,
    {
      method: "POST",
      data,
    },
  );
}

export function getMemoirVoiceSession(
  voiceSessionId: string,
): Promise<MemoirVoiceSessionDetail> {
  return memoirRequest<MemoirVoiceSessionDetail>(memoirVoiceSessionPath(voiceSessionId));
}

export function listMemoirVoiceSessions(
  options: VoiceSessionListOptions = {},
): Promise<MemoirVoiceSessionListResponse> {
  return memoirRequest<MemoirVoiceSessionListResponse>(
    `/api/mobile/memoir/voice-sessions${queryString({
      project_id: options.projectId,
      effective_only: options.effectiveOnly,
      limit: options.limit,
      offset: options.offset,
    })}`,
  );
}

export function getMemoirVoiceProgress(projectId: string): Promise<MemoirVoiceProgressResponse> {
  return memoirRequest<MemoirVoiceProgressResponse>(
    `/api/mobile/memoir/voice-progress${queryString({ project_id: projectId })}`,
  );
}

export function getMemoirProjectProgressSnapshot(projectId: string): Promise<MemoirProjectProgressSnapshot> {
  return memoirRequest<MemoirProjectProgressSnapshot>(
    `/api/mobile/memoir/projects/${encodeURIComponent(projectId)}/progress-snapshot`,
  );
}

export function listMemoirAutomationEvents(
  projectId: string,
  limit = 10,
): Promise<MemoirAutomationEventListResponse> {
  return memoirRequest<MemoirAutomationEventListResponse>(
    `/api/mobile/memoir/projects/${encodeURIComponent(projectId)}/automation-events${queryString({ limit })}`,
  );
}

export function acknowledgeMemoirAutomationEvent(
  projectId: string,
  eventId: string,
): Promise<MemoirAutomationEventListResponse["items"][number]> {
  return memoirRequest<MemoirAutomationEventListResponse["items"][number]>(
    `/api/mobile/memoir/projects/${encodeURIComponent(projectId)}/automation-events/${encodeURIComponent(eventId)}/acknowledge`,
    {
      method: "POST",
    },
  );
}

export function createMemoirVoiceNoAnswerPrompt(
  voiceSessionId: string,
  data: MemoirVoiceNoAnswerPromptRequest = {},
): Promise<MemoirVoiceFollowUpResponse> {
  return memoirRequest<MemoirVoiceFollowUpResponse>(
    memoirVoiceSessionPath(voiceSessionId, "/no-answer-prompt"),
    {
      method: "POST",
      data,
    },
  );
}

export function createMemoirAssistantTTS(
  voiceSessionId: string,
  messageId: string,
  data: MemoirAssistantTTSRequest = {},
): Promise<MemoirAssistantTTSResponse> {
  return memoirRequest<MemoirAssistantTTSResponse>(
    memoirVoiceSessionPath(voiceSessionId, `/messages/${encodeURIComponent(messageId)}/tts`),
    {
      method: "POST",
      data,
    },
  );
}
