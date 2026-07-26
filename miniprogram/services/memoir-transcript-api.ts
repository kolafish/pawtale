import type {
  MemoirShareVideoCustomBackgroundAsset,
  MemoirShareVideoJob,
  MemoirMaterialOfflineASRState,
  MemoirTranscriptShareVideoContext,
  MemoirVoiceSearchQueryParseResponse,
  MemoirVoiceSearchTranscriptionResponse,
  MemoirVoiceTranscriptIncludedFilter,
  MemoirVoiceTranscriptItem,
  MemoirVoiceTranscriptListResponse,
  MemoirVoiceTranscriptVoiceEditPreviewResponse,
} from "../domain/voice-chat";
import {
  memoirAIRequest,
  memoirRequest,
  queryString,
} from "./memoir-api-client";
import {
  downloadAuthenticatedFile,
  uploadAuthenticatedFile,
} from "./authenticated-media";
import {
  ApiError,
  cleanBaseUrl,
  readBackendConfig,
} from "./request";

interface VoiceTranscriptListOptions {
  projectId?: string;
  query?: string;
  included?: MemoirVoiceTranscriptIncludedFilter;
  guidanceMode?: string;
  module?: string;
  limit?: number;
  offset?: number;
}

interface ShareVideoJobOptions {
  backgroundSource?: string;
  backgroundStyle?: string;
  backgroundMode?: string;
  customBackgroundAssetId?: string;
}

interface ShareVideoJobRequest {
  start_char: number;
  end_char: number;
  ai_model: string | null;
  background_source?: string;
  background_style?: string;
  background_mode?: string;
  custom_background_asset_id?: string;
}

export function listMemoirVoiceTranscripts(
  options: VoiceTranscriptListOptions = {},
): Promise<MemoirVoiceTranscriptListResponse> {
  return memoirRequest<MemoirVoiceTranscriptListResponse>(
    `/api/mobile/memoir/voice-transcripts${queryString({
      project_id: options.projectId,
      q: options.query?.trim() || undefined,
      included: options.included && options.included !== "all" ? options.included : undefined,
      guidance_mode: options.guidanceMode,
      module: options.module,
      limit: options.limit,
      offset: options.offset,
    })}`,
  );
}

export function getMemoirVoiceTranscript(
  utteranceId: string,
): Promise<MemoirVoiceTranscriptItem> {
  return memoirRequest<MemoirVoiceTranscriptItem>(
    `/api/mobile/memoir/voice-transcripts/${encodeURIComponent(utteranceId)}`,
  );
}

export function startMemoirMaterialOfflineASR(
  utteranceId: string,
  aiModel?: string,
): Promise<MemoirMaterialOfflineASRState> {
  return memoirAIRequest<MemoirMaterialOfflineASRState, { ai_model: string | null }>(
    `/api/mobile/memoir/voice-transcripts/${encodeURIComponent(utteranceId)}/offline-asr`,
    { ai_model: aiModel?.trim() || null },
    { timeoutProfile: "mutation" },
  );
}

export function getMemoirTranscriptShareVideoContext(
  utteranceId: string,
): Promise<MemoirTranscriptShareVideoContext> {
  return memoirRequest<MemoirTranscriptShareVideoContext>(
    `/api/mobile/memoir/voice-transcripts/${encodeURIComponent(utteranceId)}/share-video/context`,
  );
}

export function createMemoirTranscriptShareVideoJob(
  utteranceId: string,
  startChar: number,
  endChar: number,
  options: ShareVideoJobOptions = {},
): Promise<MemoirShareVideoJob> {
  return memoirAIRequest<MemoirShareVideoJob, ShareVideoJobRequest>(
    `/api/mobile/memoir/voice-transcripts/${encodeURIComponent(utteranceId)}/share-video/jobs`,
    {
      start_char: startChar,
      end_char: endChar,
      ai_model: null,
      ...(options.backgroundSource ? { background_source: options.backgroundSource } : {}),
      ...(options.backgroundStyle ? { background_style: options.backgroundStyle } : {}),
      ...(options.backgroundMode ? { background_mode: options.backgroundMode } : {}),
      ...(options.customBackgroundAssetId ? { custom_background_asset_id: options.customBackgroundAssetId } : {}),
    },
  );
}

function uploadApiMessage(data: string): string {
  try {
    const parsed = JSON.parse(data) as { detail?: unknown };
    return typeof parsed.detail === "string" ? parsed.detail : "背景图片上传失败";
  } catch {
    return data || "背景图片上传失败";
  }
}

function parseUploadJson<T>(data: string): T {
  try {
    return JSON.parse(data) as T;
  } catch (error) {
    throw new ApiError(0, "背景图片上传结果解析失败", error);
  }
}

export function uploadMemoirShareVideoCustomBackground(
  projectId: string,
  filePath: string,
): Promise<MemoirShareVideoCustomBackgroundAsset> {
  const config = readBackendConfig();
  if (!config.httpBaseUrl) {
    throw new ApiError(0, "未配置后端 HTTP 地址", {});
  }
  const url = `${cleanBaseUrl(config.httpBaseUrl)}/api/mobile/memoir/share-video/background-assets`;
  return uploadAuthenticatedFile(url, {
    filePath,
    name: "file",
    timeoutMs: 60000,
    formData: {
      project_id: projectId,
      processing_preset: "memoir_soft",
    },
    failureMessage: "背景图片上传失败",
    domainLabel: "图片上传",
    parseResponse: parseUploadJson<MemoirShareVideoCustomBackgroundAsset>,
    responseErrorMessage: uploadApiMessage,
  });
}

export function getMemoirShareVideoJob(jobId: string): Promise<MemoirShareVideoJob> {
  return memoirRequest<MemoirShareVideoJob>(
    `/api/mobile/memoir/share-video/jobs/${encodeURIComponent(jobId)}`,
  );
}

export function memoirShareVideoDownloadUrl(downloadUrl: string): string {
  if (/^https?:\/\//.test(downloadUrl)) {
    return downloadUrl;
  }
  const config = readBackendConfig();
  if (!config.httpBaseUrl) {
    throw new ApiError(0, "未配置后端 HTTP 地址", {});
  }
  return `${cleanBaseUrl(config.httpBaseUrl)}${downloadUrl.startsWith("/") ? downloadUrl : `/${downloadUrl}`}`;
}

export function downloadMemoirShareVideoJob(job: MemoirShareVideoJob): Promise<string> {
  const downloadUrl = job.output?.download_url.trim();
  if (!downloadUrl) {
    throw new ApiError(400, "视频还没有生成完成", job);
  }
  const url = memoirShareVideoDownloadUrl(downloadUrl);
  return downloadAuthenticatedFile(url, {
    timeoutMs: 60000,
    failureMessage: "视频下载失败",
    domainLabel: "视频下载",
  });
}

export function downloadMemoirShareVideoCustomBackgroundPreview(
  asset: MemoirShareVideoCustomBackgroundAsset,
): Promise<string> {
  if (!asset.preview_url) {
    throw new ApiError(400, "背景图片预览不可用", asset);
  }
  const url = memoirShareVideoDownloadUrl(asset.preview_url);
  return downloadAuthenticatedFile(url, {
    timeoutMs: 60000,
    failureMessage: "背景图片预览下载失败",
    domainLabel: "图片预览",
  });
}

export function updateMemoirVoiceTranscript(
  utteranceId: string,
  transcriptEdited: string,
): Promise<MemoirVoiceTranscriptItem> {
  return memoirRequest<MemoirVoiceTranscriptItem>(
    `/api/mobile/memoir/voice-transcripts/${encodeURIComponent(utteranceId)}`,
    {
      method: "PATCH",
      data: { transcript_edited: transcriptEdited },
    },
  );
}

export function convertMemoirInterviewQuestionToAnswer(
  projectId: string,
  messageId: string,
): Promise<unknown> {
  return memoirRequest<unknown>(
    `/api/memoir/projects/${encodeURIComponent(projectId)}/interview-editor/questions/${encodeURIComponent(messageId)}/convert-to-answer`,
    {
      method: "POST",
      data: {
        parent_question_message_id: null,
        reason: "miniprogram_convert_question_to_unassigned_answer",
        mutation_source: "miniprogram_transcript_detail",
      },
    },
  );
}

export function recoverMemoirVoiceTranscript(
  utteranceId: string,
): Promise<MemoirVoiceTranscriptItem> {
  return memoirRequest<MemoirVoiceTranscriptItem>(
    `/api/mobile/memoir/voice-transcripts/${encodeURIComponent(utteranceId)}/recover-transcription`,
    {
      method: "POST",
      timeoutProfile: "generation",
    },
  );
}

export function previewMemoirVoiceTranscriptVoiceEdit(
  utteranceId: string,
  instructionText: string,
  aiModel?: string,
): Promise<MemoirVoiceTranscriptVoiceEditPreviewResponse> {
  return memoirAIRequest<
    MemoirVoiceTranscriptVoiceEditPreviewResponse,
    { instruction_text: string; ai_model: string | null }
  >(
    `/api/mobile/memoir/voice-transcripts/${encodeURIComponent(utteranceId)}/voice-edit/preview`,
    {
      instruction_text: instructionText,
      ai_model: aiModel?.trim() || null,
    },
  );
}

export async function deleteMemoirVoiceTranscript(
  utteranceId: string,
): Promise<void> {
  await memoirRequest<unknown>(
    `/api/mobile/memoir/voice-transcripts/${encodeURIComponent(utteranceId)}`,
    {
      method: "DELETE",
    },
  );
}

export function transcribeMemoirVoiceSearchAudio(
  audioBase64: string,
  mimeType = "audio/pcm",
): Promise<MemoirVoiceSearchTranscriptionResponse> {
  return memoirRequest<MemoirVoiceSearchTranscriptionResponse>("/api/mobile/memoir/search-query/transcribe", {
    method: "POST",
    timeoutProfile: "generation",
    data: {
      audio_base64: audioBase64,
      mime_type: mimeType,
    },
  });
}

export function parseMemoirVoiceSearchQuery(
  text: string,
  aiModel?: string,
): Promise<MemoirVoiceSearchQueryParseResponse> {
  return memoirAIRequest<
    MemoirVoiceSearchQueryParseResponse,
    { text: string; ai_model: string | null }
  >(
    "/api/mobile/memoir/search-query/parse",
    {
      text,
      ai_model: aiModel?.trim() || null,
    },
  );
}
