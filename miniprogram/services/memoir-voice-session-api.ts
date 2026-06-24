import type {
  MemoirAssistantTTSRequest,
  MemoirAssistantTTSResponse,
  MemoirVoiceFollowUpRequest,
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
  withSelectedAIModel,
} from "./memoir-api-client";
import {
  ApiError,
  cleanBaseUrl,
  readBackendConfig,
  readSessionCookie,
} from "./request";

interface VoiceSessionListOptions {
  projectId?: string;
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

export function createMemoirVoiceFollowUp(
  voiceSessionId: string,
  data: MemoirVoiceFollowUpRequest = {},
): Promise<MemoirVoiceFollowUpResponse> {
  return memoirRequest<MemoirVoiceFollowUpResponse>(
    memoirVoiceSessionPath(voiceSessionId, "/follow-up"),
    {
      method: "POST",
      data: withSelectedAIModel(data),
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
