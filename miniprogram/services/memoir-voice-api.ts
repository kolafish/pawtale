import type {
  MemoirAssistantTTSRequest,
  MemoirAssistantTTSResponse,
  MemoirBookAudioRequest,
  MemoirBookAudioResponse,
  MemoirBookChapterDraft,
  MemoirBookChapterDraftGenerateRequest,
  MemoirBookChapterDraftUpdateRequest,
  MemoirBookOutput,
  MemoirBookOutputGenerateRequest,
  MemoirBookPlan,
  MemoirBookPlanConfirmRequest,
  MemoirBookPlanGenerateRequest,
  MemoirBookPlanUpdateRequest,
  MemoirBookSourceAssignmentRequest,
  MemoirBookSourceCoverage,
  MemoirVoiceFollowUpRequest,
  MemoirVoiceFollowUpResponse,
  MemoirVoiceTranscriptItem,
  MemoirVoiceTranscriptListResponse,
  MemoirVoiceProgressResponse,
  MemoirVoiceSessionCreate,
  MemoirVoiceSessionDetail,
  MemoirVoiceSessionListResponse,
} from "../domain/voice-chat";
import { ensureBackendSession } from "./auth";
import {
  ApiError,
  cleanBaseUrl,
  readBackendConfig,
  readSessionCookie,
  request,
} from "./request";

export function voiceGatewayUrl(voiceSessionId: string): string {
  const config = readBackendConfig();
  if (!config.wsBaseUrl) {
    throw new ApiError(0, "未配置后端 WebSocket 地址", {});
  }
  return `${cleanBaseUrl(config.wsBaseUrl)}/api/mobile/memoir/voice-sessions/${encodeURIComponent(voiceSessionId)}/gateway`;
}

export async function backendSessionCookieHeader(): Promise<string> {
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

interface VoiceSessionListOptions {
  projectId?: string;
  limit?: number;
  offset?: number;
}

function voiceQueryString(options: VoiceSessionListOptions): string {
  const query: string[] = [];
  if (options.projectId) {
    query.push(`project_id=${encodeURIComponent(options.projectId)}`);
  }
  if (options.limit !== undefined) {
    query.push(`limit=${encodeURIComponent(String(options.limit))}`);
  }
  if (options.offset !== undefined) {
    query.push(`offset=${encodeURIComponent(String(options.offset))}`);
  }
  return query.length ? `?${query.join("&")}` : "";
}

export async function listMemoirVoiceSessions(
  options: VoiceSessionListOptions = {},
): Promise<MemoirVoiceSessionListResponse> {
  await ensureBackendSession();
  return request<MemoirVoiceSessionListResponse>(
    `/api/mobile/memoir/voice-sessions${voiceQueryString(options)}`,
  );
}

export async function getMemoirVoiceProgress(projectId?: string): Promise<MemoirVoiceProgressResponse> {
  await ensureBackendSession();
  const query = projectId ? `?project_id=${encodeURIComponent(projectId)}` : "";
  return request<MemoirVoiceProgressResponse>(`/api/mobile/memoir/voice-progress${query}`);
}

export async function createMemoirVoiceFollowUp(
  voiceSessionId: string,
  data: MemoirVoiceFollowUpRequest = {},
): Promise<MemoirVoiceFollowUpResponse> {
  await ensureBackendSession();
  return request<MemoirVoiceFollowUpResponse>(
    `/api/mobile/memoir/voice-sessions/${encodeURIComponent(voiceSessionId)}/follow-up`,
    {
      method: "POST",
      data,
    },
  );
}

export async function createMemoirAssistantTTS(
  voiceSessionId: string,
  messageId: string,
  data: MemoirAssistantTTSRequest = {},
): Promise<MemoirAssistantTTSResponse> {
  await ensureBackendSession();
  return request<MemoirAssistantTTSResponse>(
    `/api/mobile/memoir/voice-sessions/${encodeURIComponent(voiceSessionId)}/messages/${encodeURIComponent(messageId)}/tts`,
    {
      method: "POST",
      data,
    },
  );
}

interface VoiceTranscriptListOptions {
  projectId?: string;
  limit?: number;
  offset?: number;
}

function queryString(options: VoiceTranscriptListOptions): string {
  const query: string[] = [];
  if (options.projectId) {
    query.push(`project_id=${encodeURIComponent(options.projectId)}`);
  }
  if (options.limit !== undefined) {
    query.push(`limit=${encodeURIComponent(String(options.limit))}`);
  }
  if (options.offset !== undefined) {
    query.push(`offset=${encodeURIComponent(String(options.offset))}`);
  }
  return query.length ? `?${query.join("&")}` : "";
}

export async function listMemoirVoiceTranscripts(
  options: VoiceTranscriptListOptions = {},
): Promise<MemoirVoiceTranscriptListResponse> {
  await ensureBackendSession();
  return request<MemoirVoiceTranscriptListResponse>(
    `/api/mobile/memoir/voice-transcripts${queryString(options)}`,
  );
}

export async function getMemoirBookPlan(projectId: string): Promise<MemoirBookPlan> {
  await ensureBackendSession();
  return request<MemoirBookPlan>(
    `/api/mobile/memoir/projects/${encodeURIComponent(projectId)}/book-plan`,
  );
}

export async function generateMemoirBookPlan(
  projectId: string,
  data: MemoirBookPlanGenerateRequest = {},
): Promise<MemoirBookPlan> {
  await ensureBackendSession();
  return request<MemoirBookPlan>(
    `/api/mobile/memoir/projects/${encodeURIComponent(projectId)}/book-plan/generate`,
    {
      method: "POST",
      data,
    },
  );
}

export async function confirmMemoirBookPlan(
  projectId: string,
  data: MemoirBookPlanConfirmRequest,
): Promise<MemoirBookPlan> {
  await ensureBackendSession();
  return request<MemoirBookPlan>(
    `/api/mobile/memoir/projects/${encodeURIComponent(projectId)}/book-plan`,
    {
      method: "PUT",
      data,
    },
  );
}

export async function updateMemoirBookPlan(
  projectId: string,
  data: MemoirBookPlanUpdateRequest,
): Promise<MemoirBookPlan> {
  await ensureBackendSession();
  return request<MemoirBookPlan>(
    `/api/mobile/memoir/projects/${encodeURIComponent(projectId)}/book-plan`,
    {
      method: "PATCH",
      data,
    },
  );
}

export async function getMemoirBookSourceCoverage(projectId: string): Promise<MemoirBookSourceCoverage> {
  await ensureBackendSession();
  return request<MemoirBookSourceCoverage>(
    `/api/mobile/memoir/projects/${encodeURIComponent(projectId)}/book-source-coverage`,
  );
}

export async function assignMemoirBookSources(
  projectId: string,
  data: MemoirBookSourceAssignmentRequest,
): Promise<MemoirBookPlan> {
  await ensureBackendSession();
  return request<MemoirBookPlan>(
    `/api/mobile/memoir/projects/${encodeURIComponent(projectId)}/book-source-assignments`,
    {
      method: "POST",
      data,
    },
  );
}

export async function getMemoirBookChapterDraft(
  projectId: string,
  chapterId: string,
): Promise<MemoirBookChapterDraft> {
  await ensureBackendSession();
  return request<MemoirBookChapterDraft>(
    `/api/mobile/memoir/projects/${encodeURIComponent(projectId)}/book-plan/chapters/${encodeURIComponent(chapterId)}/draft`,
  );
}

export async function generateMemoirBookChapterDraft(
  projectId: string,
  chapterId: string,
  data: MemoirBookChapterDraftGenerateRequest = {},
): Promise<MemoirBookChapterDraft> {
  await ensureBackendSession();
  return request<MemoirBookChapterDraft>(
    `/api/mobile/memoir/projects/${encodeURIComponent(projectId)}/book-plan/chapters/${encodeURIComponent(chapterId)}/draft`,
    {
      method: "POST",
      data,
    },
  );
}

export async function updateMemoirBookChapterDraft(
  projectId: string,
  chapterId: string,
  data: MemoirBookChapterDraftUpdateRequest,
): Promise<MemoirBookChapterDraft> {
  await ensureBackendSession();
  return request<MemoirBookChapterDraft>(
    `/api/mobile/memoir/projects/${encodeURIComponent(projectId)}/book-plan/chapters/${encodeURIComponent(chapterId)}/draft`,
    {
      method: "PATCH",
      data,
    },
  );
}

export async function confirmMemoirBookChapterDraft(
  projectId: string,
  chapterId: string,
): Promise<MemoirBookChapterDraft> {
  await ensureBackendSession();
  return request<MemoirBookChapterDraft>(
    `/api/mobile/memoir/projects/${encodeURIComponent(projectId)}/book-plan/chapters/${encodeURIComponent(chapterId)}/draft/confirm`,
    {
      method: "POST",
    },
  );
}

export async function createMemoirBookChapterDraftTTS(
  projectId: string,
  chapterId: string,
  data: MemoirBookAudioRequest = {},
): Promise<MemoirBookAudioResponse> {
  await ensureBackendSession();
  return request<MemoirBookAudioResponse>(
    `/api/mobile/memoir/projects/${encodeURIComponent(projectId)}/book-plan/chapters/${encodeURIComponent(chapterId)}/draft/tts`,
    {
      method: "POST",
      data,
    },
  );
}

export async function getMemoirBookOutput(projectId: string): Promise<MemoirBookOutput> {
  await ensureBackendSession();
  return request<MemoirBookOutput>(
    `/api/mobile/memoir/projects/${encodeURIComponent(projectId)}/book-output`,
  );
}

export async function generateMemoirBookOutput(
  projectId: string,
  data: MemoirBookOutputGenerateRequest = {},
): Promise<MemoirBookOutput> {
  await ensureBackendSession();
  return request<MemoirBookOutput>(
    `/api/mobile/memoir/projects/${encodeURIComponent(projectId)}/book-output/generate`,
    {
      method: "POST",
      data,
    },
  );
}

export async function createMemoirBookOutputTTS(
  projectId: string,
  data: MemoirBookAudioRequest = {},
): Promise<MemoirBookAudioResponse> {
  await ensureBackendSession();
  return request<MemoirBookAudioResponse>(
    `/api/mobile/memoir/projects/${encodeURIComponent(projectId)}/book-output/tts`,
    {
      method: "POST",
      data,
    },
  );
}

export async function updateMemoirVoiceTranscript(
  utteranceId: string,
  transcriptEdited: string,
): Promise<MemoirVoiceTranscriptItem> {
  await ensureBackendSession();
  return request<MemoirVoiceTranscriptItem>(
    `/api/mobile/memoir/voice-transcripts/${encodeURIComponent(utteranceId)}`,
    {
      method: "PATCH",
      data: { transcript_edited: transcriptEdited },
    },
  );
}

export async function deleteMemoirVoiceTranscript(
  utteranceId: string,
): Promise<void> {
  await ensureBackendSession();
  await request<unknown>(
    `/api/mobile/memoir/voice-transcripts/${encodeURIComponent(utteranceId)}`,
    {
      method: "DELETE",
    },
  );
}

export function memoirAudioPlaybackUrl(audioFileId: string): string {
  const config = readBackendConfig();
  if (!config.httpBaseUrl) {
    throw new ApiError(0, "未配置后端 HTTP 地址", {});
  }
  return `${cleanBaseUrl(config.httpBaseUrl)}/api/mobile/memoir/audio-files/${encodeURIComponent(audioFileId)}/playback`;
}

export async function downloadMemoirVoiceAudio(audioFileId: string): Promise<string> {
  await ensureBackendSession();
  const cookie = readSessionCookie();
  return new Promise((resolve, reject) => {
    wx.downloadFile({
      url: memoirAudioPlaybackUrl(audioFileId),
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
        reject(new ApiError(0, error.errMsg || "音频下载失败", error));
      },
    });
  });
}
