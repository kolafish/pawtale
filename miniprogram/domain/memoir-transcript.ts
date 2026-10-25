import type {
  MemoirChatMessageRole,
  MemoirGuidanceMode,
  MemoirInterviewType,
  MemoirVoiceSessionStatus,
} from "./memoir-voice-session";

export type MemoirVoiceTranscriptIncludedFilter = "all" | "included" | "not_included";
export type MemoirMaterialOfflineASRStatus = "idle" | "pending" | "running" | "succeeded" | "failed";

export type MemoirShareVideoStyle = "uploaded" | "illustrated";

export interface MemoirShareVideoStyleOption {
  value: MemoirShareVideoStyle;
  label: string;
  description: string;
}

export const MEMOIR_SHARE_VIDEO_STYLES: MemoirShareVideoStyleOption[] = [
  { value: "uploaded", label: "自己配图", description: "使用自己上传的照片或插图，配上讲述原声和字幕。" },
  { value: "illustrated", label: "AI配图", description: "AI 根据讲述内容生成插图，配上讲述原声和字幕。" },
];

export function normalizeMemoirShareVideoStyle(value: unknown, backgroundSource?: string): MemoirShareVideoStyle {
  if (value === "uploaded" || value === "illustrated") {
    return value;
  }
  return backgroundSource === "custom" ? "uploaded" : "illustrated";
}

export interface MemoirMaterialOfflineASRState {
  status: MemoirMaterialOfflineASRStatus;
  job_id?: string | null;
  can_start: boolean;
  blocked_reason: string;
  providers: string[];
  narrator_origin: string;
  dialect_code: string;
  dialect_label: string;
  dialect_strategy: string;
  dialect_enhancement_enabled: boolean;
  last_applied_at?: string | null;
  error_message: string;
}

export interface MemoirVoiceTranscriptItem {
  id: string;
  voice_session_id: string;
  project_id: string;
  project_title: string;
  chat_message_id?: string | null;
  chat_message_role?: MemoirChatMessageRole | null;
  audio_file_id?: string | null;
  audio_mime_type?: string | null;
  audio_size_bytes?: number | null;
  audio_duration_seconds?: number | null;
  transcript_version_id?: string | null;
  question_message_id?: string | null;
  question_text: string;
  question_audio_file_id?: string | null;
  transcript_raw: string;
  transcript_edited: string;
  display_text: string;
  summary_title: string;
  summary_text: string;
  summary_source: string;
  transcription_status?: "ready" | "pending_transcription" | "unrecognized" | "empty";
  can_recover_transcription?: boolean;
  raw_char_count: number;
  edited_char_count: number;
  display_char_count: number;
  start_ms?: number | null;
  end_ms?: number | null;
  provider?: string | null;
  session_status: MemoirVoiceSessionStatus;
  guidance_mode: MemoirGuidanceMode;
  interview_type: MemoirInterviewType;
  current_guidance_module?: string | null;
  is_included_in_book?: boolean;
  included_chapters?: MemoirBookSourceIncludedChapter[];
  section_assignment?: MemoirVoiceTranscriptSectionAssignment | null;
  offline_asr: MemoirMaterialOfflineASRState;
  created_at: string;
  session_created_at: string;
}

export interface MemoirVoiceTranscriptListResponse {
  empty_count?: number;
  items: MemoirVoiceTranscriptItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface MemoirTranscriptShareVideoSegment {
  index: number;
  text: string;
  start_char: number;
  end_char: number;
  start_ms: number;
  end_ms: number;
  confidence?: number | null;
  selectable: boolean;
}

export interface MemoirShareVideoBackgroundStyle {
  value: string;
  label: string;
  description: string;
}

export interface MemoirShareVideoBackgroundMode {
  value: string;
  label: string;
  description: string;
}

export interface MemoirShareVideoBackgroundSource {
  value: string;
  label: string;
  description: string;
}

export interface MemoirTranscriptShareVideoContext {
  utterance_id: string;
  audio_file_id: string;
  latest_job_id?: string | null;
  latest_image_job_id?: string | null;
  transcript_source: string;
  transcript_text: string;
  text_hash: string;
  duration_ms: number;
  alignment_strategy: string;
  alignment_confidence?: number | null;
  min_select_ms: number;
  max_select_ms: number;
  photo_min_ms?: number;
  photo_max_count?: number;
  default_video_style: MemoirShareVideoStyle;
  video_styles: MemoirShareVideoStyleOption[];
  ai_background_enabled: boolean;
  default_background_source: string;
  default_background_style: string;
  default_background_mode: string;
  background_sources: MemoirShareVideoBackgroundSource[];
  background_styles: MemoirShareVideoBackgroundStyle[];
  background_modes: MemoirShareVideoBackgroundMode[];
  segments: MemoirTranscriptShareVideoSegment[];
}

export type MemoirMaterialImageGenerationJobStatus =
  | "pending"
  | "running"
  | "succeeded"
  | "failed"
  | "skipped";

export interface MemoirMaterialImageGenerationJob {
  job_id: string;
  project_id: string;
  utterance_id: string;
  asset_id: string;
  status: MemoirMaterialImageGenerationJobStatus;
  asset_status: "pending" | "running" | "ready" | "failed" | "deleted";
  stage: string;
  selected_text: string;
  start_char: number;
  end_char: number;
  style_id: string;
  error_message: string;
  created_at: string;
  updated_at: string;
}

export type MemoirShareVideoJobStatus = "pending" | "running" | "succeeded" | "failed";
export interface MemoirShareVideoCandidate {
  id: string;
  project_id: string;
  voice_session_id: string;
  utterance_id: string | null;
  audio_file_id: string | null;
  transcript_version_id: string | null;
  text_hash: string | null;
  selected_text: string;
  start_char: number | null;
  end_char: number | null;
  start_ms: number | null;
  end_ms: number | null;
  duration_ms: number | null;
  status: "ready" | "accepted" | "dismissed" | "stale" | "none";
  reason: string;
  hook_text: string;
  video_job_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface MemoirShareVideoCandidateResponse {
  candidate: MemoirShareVideoCandidate | null;
}

export interface MemoirShareVideoOutput {
  mime_type: string;
  size_bytes: number;
  download_url: string;
  share_code: string;
  share_enabled: boolean;
}

export interface MemoirSharedVideo {
  title: string;
  book_title: string;
  narrator_name: string;
  duration_ms: number;
  video_url: string;
  poster_url: string;
  end_frame_url: string;
}

export interface MemoirShareVideoJob {
  id: string;
  project_id: string;
  utterance_id: string;
  audio_file_id: string;
  status: MemoirShareVideoJobStatus;
  title: string;
  selected_text: string;
  start_char: number;
  end_char: number;
  start_ms: number;
  end_ms: number;
  duration_ms: number;
  video_style: MemoirShareVideoStyle;
  background_source?: string;
  background_style?: string;
  background_mode?: string;
  background_effective_mode?: string;
  photo_asset_ids?: string[];
  background_note?: string;
  background_style_label?: string;
  background_scene_count?: number;
  background_status?: string;
  background_provider?: string | null;
  background_model?: string | null;
  background_cache_hit?: boolean | null;
  background_error_message?: string;
  output: MemoirShareVideoOutput | null;
  error_message: string;
  created_at: string;
  updated_at: string;
  completed_at?: string | null;
}

export interface MemoirShareVideoListItem {
  id: string;
  utterance_id: string;
  title: string;
  selected_text: string;
  duration_ms: number;
  video_style: MemoirShareVideoStyle;
  // The illustration style an AI-illustrated video was made in; empty otherwise.
  background_style_label?: string;
  created_at: string;
  completed_at: string | null;
  share_code: string;
  // Family likes from the forwarded card; absent on videos still being made.
  like_count?: number;
}

export interface MemoirShareVideoListResponse {
  items: MemoirShareVideoListItem[];
  active_jobs: MemoirShareVideoJob[];
  failed_jobs: MemoirShareVideoJob[];
  total: number;
  limit: number;
  offset: number;
}

export interface MemoirVoiceSearchTranscriptionResponse {
  transcript: string;
}

export type MemoirNarrationMode = "ai_interview" | "human_interview" | "book_completion";

export interface MemoirVoiceSearchQueryFilters {
  narration_mode?: MemoirNarrationMode | null;
  included: MemoirVoiceTranscriptIncludedFilter;
  guidance_mode?: MemoirGuidanceMode | null;
  module?: string | null;
}

export interface MemoirVoiceSearchQueryParseResponse {
  original_text: string;
  query: string;
  keywords: string[];
  filters: MemoirVoiceSearchQueryFilters;
  display_text: string;
  used_ai: boolean;
  provider?: string | null;
  model?: string | null;
}

export type MemoirVoiceTranscriptVoiceEditAction = "revise_transcript" | "clarify" | "unsupported";
export type MemoirVoiceTranscriptVoiceEditScope = "term" | "sentence" | "passage";

export interface MemoirVoiceTranscriptVoiceEditChange {
  scope: MemoirVoiceTranscriptVoiceEditScope;
  before: string;
  after: string;
}

export interface MemoirVoiceTranscriptVoiceEditPreviewResponse {
  utterance_id: string;
  action: MemoirVoiceTranscriptVoiceEditAction;
  original_text: string;
  current_text: string;
  edited_text: string;
  changes: MemoirVoiceTranscriptVoiceEditChange[];
  confidence: number;
  requires_confirmation: boolean;
  reply_text: string;
  provider?: string | null;
  model?: string | null;
}

export interface MemoirBookSourceIncludedChapter {
  chapter_id: string;
  title: string;
  sort_order: number;
  include_in_book?: boolean;
}

export interface MemoirVoiceTranscriptSectionAssignment {
  chapter_id: string;
  chapter_title: string;
  chapter_sort_order: number;
  section_id: string;
  section_title: string;
  section_sort_order: number;
  requires_user_assignment: boolean;
}
