export type MemoirGuidanceMode = "free_narration" | "guided_interview";
export type MemoirVoiceSessionStatus = "created" | "listening" | "speaking" | "stopped" | "failed";
export type MemoirChatMessageRole = "user" | "assistant" | "system";
export type MemoirChatContentStatus = "partial" | "final" | "corrected";
export type MemoirCreatedBy = "user" | "ai" | "asr" | "system";
export type MemoirBookPlanStatus = "draft" | "confirmed";
export type MemoirBookChapterDraftStatus = "draft" | "confirmed";
export type MemoirOutputStatus = "draft" | "accepted" | "archived";
export type VoiceGatewayState = "ready" | "listening" | "finishing" | "stopped" | "failed";
export type VoiceChatErrorKind = "connection" | "recorder" | "gateway" | "asr" | "persistence" | "timeout" | "unknown";

export interface MemoirVoiceSession {
  id: string;
  project_id: string;
  memoir_session_id?: string | null;
  status: MemoirVoiceSessionStatus;
  guidance_mode: MemoirGuidanceMode;
  current_guidance_module?: string | null;
  provider?: string | null;
  input_format?: string | null;
  started_at?: string | null;
  stopped_at?: string | null;
  error_message: string;
  metadata_json: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface MemoirChatMessage {
  id: string;
  project_id: string;
  voice_session_id: string;
  role: MemoirChatMessageRole;
  content_text: string;
  content_status: MemoirChatContentStatus;
  audio_file_id?: string | null;
  transcript_version_id?: string | null;
  parent_message_id?: string | null;
  guidance_intent?: string | null;
  turn_index: number;
  metadata_json: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface MemoirVoiceUtterance {
  id: string;
  voice_session_id: string;
  chat_message_id?: string | null;
  audio_file_id?: string | null;
  transcript_raw: string;
  transcript_edited: string;
  start_ms?: number | null;
  end_ms?: number | null;
  provider?: string | null;
  provider_payload_json: Record<string, unknown>;
  created_at: string;
}

export interface MemoirVoiceSessionDetail {
  session: MemoirVoiceSession;
  messages: MemoirChatMessage[];
  utterances: MemoirVoiceUtterance[];
}

export interface MemoirVoiceSessionSummary {
  id: string;
  project_id: string;
  status: MemoirVoiceSessionStatus;
  guidance_mode: MemoirGuidanceMode;
  current_guidance_module?: string | null;
  started_at?: string | null;
  stopped_at?: string | null;
  created_at: string;
  updated_at: string;
  user_message_count: number;
  assistant_message_count: number;
  utterance_count: number;
  total_user_char_count: number;
  total_audio_seconds: number;
  last_message_text: string;
  last_message_role?: MemoirChatMessageRole | null;
  last_message_at?: string | null;
}

export interface MemoirVoiceSessionListResponse {
  items: MemoirVoiceSessionSummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface MemoirVoiceModuleProgress {
  module: string;
  session_count: number;
  utterance_count: number;
  user_char_count: number;
}

export interface MemoirVoiceProgressResponse {
  project_id?: string | null;
  session_count: number;
  active_session_count: number;
  stopped_session_count: number;
  failed_session_count: number;
  utterance_count: number;
  user_message_count: number;
  assistant_message_count: number;
  total_user_char_count: number;
  total_audio_seconds: number;
  last_session_at?: string | null;
  modules: MemoirVoiceModuleProgress[];
}

export interface MemoirVoiceSessionCreate {
  memoir_session_id?: string;
  guidance_mode: MemoirGuidanceMode;
  current_guidance_module?: string;
  provider?: string;
  input_format?: string;
  metadata_json?: Record<string, unknown>;
}

export interface MemoirVoiceFollowUpRequest {
  ai_model?: string;
  regenerate?: boolean;
}

export interface MemoirVoiceFollowUpResponse {
  message: MemoirChatMessage;
  follow_up_text: string;
}

export interface MemoirAssistantTTSRequest {
  voice?: string;
  force_regenerate?: boolean;
}

export interface TTSAlignmentSegment {
  text: string;
  start_ms: number;
  end_ms: number;
  text_start: number;
  text_end: number;
  confidence?: number | null;
}

export interface MemoirAssistantTTSResponse {
  message: MemoirChatMessage;
  audio_file_id: string;
  provider: string;
  model: string;
  mime_type: string;
  duration_seconds?: number | null;
  alignment_provider?: string | null;
  alignment?: TTSAlignmentSegment[];
}

export type VoiceGatewayClientEvent =
  | { type: "session.start"; guidance_mode: MemoirGuidanceMode; current_guidance_module?: string }
  | { type: "audio.frame"; audio: string }
  | { type: "audio.end" }
  | { type: "session.stop" };

export interface VoiceGatewayServerEvent {
  type: string;
  state?: VoiceGatewayState;
  status?: string;
  text?: string;
  message_id?: string;
  utterance_id?: string;
  audio_file_id?: string | null;
  transcript_version_id?: string | null;
  start_ms?: number | null;
  end_ms?: number | null;
  error?: string;
  detail?: Record<string, unknown>;
  provider_payload?: Record<string, unknown>;
  provider_event_type?: string;
}

export interface VoiceChatErrorInfo {
  kind: VoiceChatErrorKind;
  message: string;
  code?: string;
  state?: VoiceGatewayState;
}

export interface VoiceChatTranscriptLine {
  id: string;
  text: string;
  role?: MemoirChatMessageRole;
  message_id?: string;
  utterance_id?: string;
  audio_file_id?: string | null;
  transcript_version_id?: string | null;
  metadata_json?: Record<string, unknown>;
  start_ms?: number | null;
  end_ms?: number | null;
}

export interface MemoirVoiceTranscriptItem {
  id: string;
  voice_session_id: string;
  project_id: string;
  project_title: string;
  chat_message_id?: string | null;
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
  raw_char_count: number;
  edited_char_count: number;
  display_char_count: number;
  start_ms?: number | null;
  end_ms?: number | null;
  provider?: string | null;
  session_status: MemoirVoiceSessionStatus;
  guidance_mode: MemoirGuidanceMode;
  current_guidance_module?: string | null;
  created_at: string;
  session_created_at: string;
}

export interface MemoirVoiceTranscriptListResponse {
  items: MemoirVoiceTranscriptItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface MemoirBookChapterSourceRef {
  voice_session_id: string;
  message_id: string;
  utterance_id: string;
  transcript_version_id?: string | null;
  audio_file_id?: string | null;
  start_ms?: number | null;
  end_ms?: number | null;
  quote: string;
}

export interface MemoirBookPlanChapterInput {
  title: string;
  summary: string;
  source_refs: MemoirBookChapterSourceRef[];
}

export interface MemoirBookPlanChapter {
  id: string;
  plan_id: string;
  project_id: string;
  title: string;
  summary: string;
  sort_order: number;
  source_refs: MemoirBookChapterSourceRef[];
  metadata_json: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface MemoirBookPlan {
  id: string;
  project_id: string;
  title: string;
  status: MemoirBookPlanStatus;
  created_by: MemoirCreatedBy;
  model?: string | null;
  metadata_json: Record<string, unknown>;
  chapters: MemoirBookPlanChapter[];
  created_at: string;
  updated_at: string;
}

export interface MemoirBookSourceCoverageItem {
  source_index: number;
  voice_session_id: string;
  message_id: string;
  utterance_id: string;
  transcript_version_id?: string | null;
  audio_file_id?: string | null;
  start_ms?: number | null;
  end_ms?: number | null;
  text: string;
  created_at: string;
}

export interface MemoirBookSourceCoverage {
  project_id: string;
  plan_id?: string | null;
  total_source_count: number;
  covered_source_count: number;
  uncovered_source_count: number;
  uncovered_sources: MemoirBookSourceCoverageItem[];
}

export interface MemoirBookSourceAssignmentInput {
  chapter_id: string;
  utterance_ids: string[];
}

export interface MemoirBookSourceAssignmentRequest {
  assignments: MemoirBookSourceAssignmentInput[];
}

export interface MemoirBookPlanGenerateRequest {
  ai_model?: string;
  max_chapters?: number;
}

export interface MemoirBookPlanConfirmRequest {
  title?: string;
  chapters: MemoirBookPlanChapterInput[];
}

export interface MemoirBookChapterDraftGenerateRequest {
  ai_model?: string;
  force_regenerate?: boolean;
  feedback?: string;
}

export interface MemoirBookChapterDraftUpdateRequest {
  content: string;
}

export interface MemoirBookChapterDraftVersion {
  id: string;
  draft_id: string;
  project_id: string;
  chapter_id: string;
  version: number;
  content: string;
  created_by: MemoirCreatedBy;
  model?: string | null;
  prompt_snapshot: string;
  feedback?: string | null;
  created_at: string;
}

export interface MemoirBookChapterDraft {
  id: string;
  project_id: string;
  chapter_id: string;
  status: MemoirBookChapterDraftStatus;
  metadata_json: Record<string, unknown>;
  latest_version?: MemoirBookChapterDraftVersion | null;
  versions: MemoirBookChapterDraftVersion[];
  created_at: string;
  updated_at: string;
}

export interface MemoirBookAudioRequest {
  voice?: string;
  force_regenerate?: boolean;
}

export interface MemoirBookAudioResponse {
  audio_file_ids: string[];
  provider: string;
  model: string;
  mime_type: string;
  chunk_count: number;
  source_text_hash: string;
}

export interface MemoirBookOutputGenerateRequest {
  force_regenerate?: boolean;
}

export interface MemoirBookOutputVersion {
  id: string;
  output_id: string;
  project_id: string;
  plan_id: string;
  version: number;
  content: string;
  chapter_snapshot_json: Record<string, unknown>[];
  created_by: MemoirCreatedBy;
  created_at: string;
}

export interface MemoirBookOutput {
  id: string;
  project_id: string;
  plan_id: string;
  title: string;
  status: MemoirOutputStatus;
  metadata_json: Record<string, unknown>;
  latest_version?: MemoirBookOutputVersion | null;
  versions: MemoirBookOutputVersion[];
  created_at: string;
  updated_at: string;
}
