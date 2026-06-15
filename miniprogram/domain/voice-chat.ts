export type MemoirGuidanceMode = "free_narration" | "guided_interview";
export type MemoirVoiceSessionStatus = "created" | "listening" | "speaking" | "stopped" | "failed";
export type MemoirChatMessageRole = "user" | "assistant" | "system";
export type MemoirChatContentStatus = "partial" | "final" | "corrected";
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

export interface MemoirVoiceSessionCreate {
  memoir_session_id?: string;
  guidance_mode: MemoirGuidanceMode;
  current_guidance_module?: string;
  provider?: string;
  input_format?: string;
  metadata_json?: Record<string, unknown>;
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
  message_id?: string;
  utterance_id?: string;
  audio_file_id?: string | null;
  transcript_version_id?: string | null;
  start_ms?: number | null;
  end_ms?: number | null;
}
