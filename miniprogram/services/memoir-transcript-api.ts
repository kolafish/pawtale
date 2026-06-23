import type {
  MemoirVoiceSearchQueryParseResponse,
  MemoirVoiceSearchTranscriptionResponse,
  MemoirVoiceTranscriptIncludedFilter,
  MemoirVoiceTranscriptItem,
  MemoirVoiceTranscriptListResponse,
} from "../domain/voice-chat";
import {
  memoirRequest,
  queryString,
} from "./memoir-api-client";
import { readSelectedAIModel } from "./ai-model-settings";

interface VoiceTranscriptListOptions {
  projectId?: string;
  query?: string;
  included?: MemoirVoiceTranscriptIncludedFilter;
  guidanceMode?: string;
  module?: string;
  limit?: number;
  offset?: number;
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
  const selectedModel = aiModel?.trim() || readSelectedAIModel();
  return memoirRequest<MemoirVoiceSearchQueryParseResponse>("/api/mobile/memoir/search-query/parse", {
    method: "POST",
    data: {
      text,
      ...(selectedModel ? { ai_model: selectedModel } : {}),
    },
  });
}
