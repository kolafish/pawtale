import type { TTSAlignmentSegment } from "./memoir-audio";
import type { MemoirBookChapterDraftStatus } from "./memoir-book";
import type { MemoirTranscriptPolishLevel } from "./memoir";

export type MemoirGuidanceMode = "free_narration" | "guided_interview";
export type MemoirInterviewType = "ai_interview" | "human_interview";
export type MemoirSessionGoal = "open_narration" | "book_completion";
export type MemoirSpeakerRole = "narrator" | "interviewer";
export type MemoirGuidanceModule = "person" | "event" | "place" | "period" | "theme";
export type MemoirVoiceSessionStatus = "created" | "listening" | "speaking" | "stopped" | "failed";
export type MemoirChatMessageRole = "user" | "assistant" | "interviewer" | "system";
export type MemoirChatContentStatus = "partial" | "final" | "corrected";
export type MemoirVoiceAssistantIntent =
  | "opening"
  | "follow_up"
  | "acknowledge"
  | "encourage_continue"
  | "reflect_summary"
  | "transition"
  | "repair";
export type VoiceGatewayState = "ready" | "listening" | "finishing" | "stopped" | "failed";
export type VoiceChatErrorKind = "connection" | "recorder" | "gateway" | "asr" | "persistence" | "timeout" | "unknown";
export interface MemoirVoiceSession {
  id: string;
  project_id: string;
  status: MemoirVoiceSessionStatus;
  guidance_mode: MemoirGuidanceMode;
  interview_type: MemoirInterviewType;
  session_goal: MemoirSessionGoal;
  current_completion_issue_id?: string | null;
  anchor_framework_item_id?: string | null;
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
  completion_issue_id?: string | null;
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
  interview_type: MemoirInterviewType;
  session_goal: MemoirSessionGoal;
  current_completion_issue_id?: string | null;
  anchor_framework_item_id?: string | null;
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

export type MemoirAutomationEventType =
  | "chapter_draft_generated"
  | "book_output_generated"
  | "book_editorial_copy_generated"
  | "story_structure_updated";
export type MemoirAutomationEventStatus = "pending" | "completed" | "failed" | "needs_review";

export interface MemoirAutomationEvent {
  id: string;
  project_id: string;
  event_type: MemoirAutomationEventType;
  status: MemoirAutomationEventStatus;
  chapter_id?: string | null;
  editing_job_id?: string | null;
  utterance_ids: string[];
  summary_text: string;
  confidence?: number | null;
  metadata_json: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface MemoirAutomationEventListResponse {
  items: MemoirAutomationEvent[];
  total: number;
  limit: number;
}

export interface MemoirProjectProgressChapter {
  chapter_id: string;
  title: string;
  sort_order: number;
  include_in_book: boolean;
  source_count: number;
  pending_source_count: number;
  pending_completion_source_count: number;
  pending_new_source_count: number;
  removed_source_count: number;
  draft_status?: MemoirBookChapterDraftStatus | string | null;
  draft_word_count: number;
}

export interface MemoirProjectProgressSnapshot {
  project_id: string;
  valid_utterance_count: number;
  assigned_utterance_count: number;
  pending_utterance_count: number;
  session_count: number;
  active_session_count: number;
  stopped_session_count: number;
  failed_session_count: number;
  user_message_count: number;
  assistant_message_count: number;
  source_char_count: number;
  total_audio_seconds: number;
  chapter_count: number;
  confirmed_chapter_count: number;
  draft_word_count: number;
  last_session_at?: string | null;
  recent_automation_events: MemoirAutomationEvent[];
  chapter_progress_items: MemoirProjectProgressChapter[];
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
  snapshot?: MemoirProjectProgressSnapshot | null;
}

export interface MemoirVoiceSessionCreate {
  guidance_mode: MemoirGuidanceMode;
  interview_type?: MemoirInterviewType;
  session_goal?: MemoirSessionGoal;
  current_completion_issue_id?: string;
  anchor_framework_item_id?: string;
  current_guidance_module?: string;
  provider?: string;
  input_format?: string;
  metadata_json?: Record<string, unknown>;
}

export interface MemoirVoiceNoAnswerPromptRequest {
  attempt?: number;
}

export interface MemoirVoiceFollowUpResponse {
  message: MemoirChatMessage;
  follow_up_text: string;
  primary_intent?: MemoirVoiceAssistantIntent | string;
  intent_tags?: string[];
  expects_answer?: boolean;
}

export interface MemoirAssistantTTSRequest {
  voice?: string;
  speed_ratio?: number;
  force_regenerate?: boolean;
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
  | {
      type: "session.start";
      guidance_mode: MemoirGuidanceMode;
      current_guidance_module?: string;
      speaker_role?: MemoirSpeakerRole;
      parent_message_id?: string;
      ai_model?: string;
      transcript_polish_level?: MemoirTranscriptPolishLevel;
    }
  | { type: "barge_in.start" | "barge_in.stop"; monitor_id: string }
  | { type: "barge_in.frame"; monitor_id: string; audio: string }
  | { type: "audio.frame"; audio: string }
  | { type: "audio.end"; speech_detected?: boolean }
  | { type: "session.stop" };

export interface VoiceGatewayServerEvent {
  monitor_id?: string;
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
  segment_final?: boolean;
  turn_final?: boolean;
  speaker_role?: MemoirSpeakerRole;
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
