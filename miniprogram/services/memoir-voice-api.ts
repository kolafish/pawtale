import type {
  MemoirAssistantTTSRequest,
  MemoirAssistantTTSResponse,
  MemoirBookAudioRequest,
  MemoirBookAudioResponse,
  MemoirBookChapterDraft,
  MemoirBookChapterDraftGenerateRequest,
  MemoirBookChapterDraftUpdateRequest,
  MemoirBookChapterTitleGenerateRequest,
  MemoirBookChapterTitleUpdateRequest,
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
  options: { projectId?: string } = {},
): Promise<MemoirVoiceSessionDetail> {
  await ensureBackendSession();
  const query = options.projectId ? `?project_id=${encodeURIComponent(options.projectId)}` : "";
  return request<MemoirVoiceSessionDetail>(`/api/mobile/memoir/voice-sessions${query}`, {
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

export async function updateMemoirBookChapterTitle(
  projectId: string,
  chapterId: string,
  data: MemoirBookChapterTitleUpdateRequest,
): Promise<MemoirBookPlan> {
  await ensureBackendSession();
  return request<MemoirBookPlan>(
    `/api/mobile/memoir/projects/${encodeURIComponent(projectId)}/book-plan/chapters/${encodeURIComponent(chapterId)}/title`,
    {
      method: "PATCH",
      data,
    },
  );
}

export async function generateMemoirBookChapterTitle(
  projectId: string,
  chapterId: string,
  data: MemoirBookChapterTitleGenerateRequest = {},
): Promise<MemoirBookPlan> {
  await ensureBackendSession();
  return request<MemoirBookPlan>(
    `/api/mobile/memoir/projects/${encodeURIComponent(projectId)}/book-plan/chapters/${encodeURIComponent(chapterId)}/title/generate`,
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
  await ensureBackendSession();
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
