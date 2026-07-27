import type { MemoirChapterCompletionState } from "./chapter-completion";

export type MemoirGuidanceMode = "free_narration" | "guided_interview";
export type MemoirInterviewType = "ai_interview" | "human_interview";
export type MemoirSessionGoal = "open_narration" | "book_completion";
export type MemoirSpeakerRole = "narrator" | "interviewer";
export type MemoirGuidanceModule = "person" | "event" | "place" | "period" | "theme";
export type MemoirVoiceTranscriptIncludedFilter = "all" | "included" | "not_included";
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
export type MemoirCreatedBy = "user" | "ai" | "asr" | "system";
export type MemoirBookPlanStatus = "draft" | "confirmed";
export type MemoirBookChapterDraftStatus = "draft" | "confirmed";
export type MemoirOutputStatus = "draft" | "accepted" | "archived";
export type VoiceGatewayState = "ready" | "listening" | "finishing" | "stopped" | "failed";
export type VoiceChatErrorKind = "connection" | "recorder" | "gateway" | "asr" | "persistence" | "timeout" | "unknown";
export type MemoirCompletionIssueStatus =
  | "open"
  | "in_progress"
  | "closed";
export type MemoirCompletionIssueOutcome =
  | "answered"
  | "resolved_by_material"
  | "confirmed"
  | "not_remembered"
  | "deferred"
  | "declined"
  | "superseded";
export type MemoirCompletionIssueScope = "section" | "chapter" | "book";
export type MemoirCompletionIssueChannel = "interview" | "manual_review";
export type MemoirCompletionIssueType =
  | "context"
  | "sequence"
  | "relationship"
  | "motivation"
  | "impact"
  | "meaning"
  | "clarity"
  | "structure"
  | "privacy"
  | "conflict";
export type MemoirCompletionIssuePriority = "high" | "medium" | "low";

export type MemoirBookCompletionState = MemoirChapterCompletionState;
export type MemoirBookCompletionAction =
  | "start_interview"
  | "wait"
  | "prepare"
  | "open_chapter"
  | "retry"
  | "none";

export interface MemoirBookCompletion {
  project_id: string;
  state: MemoirBookCompletionState;
  action: MemoirBookCompletionAction;
  headline: string;
  detail: string;
  item_id?: string | null;
  chapter_id?: string | null;
}

export interface MemoirCompletionIssue {
  id: string;
  project_id: string;
  scope: MemoirCompletionIssueScope;
  section_id?: string | null;
  chapter_id?: string | null;
  issue_type: MemoirCompletionIssueType;
  channel: MemoirCompletionIssueChannel;
  question: string;
  reason: string;
  priority: MemoirCompletionIssuePriority;
  related_material_ids: string[];
  input_signature: string;
  status: MemoirCompletionIssueStatus;
  active_session_id?: string | null;
  outcome?: MemoirCompletionIssueOutcome | null;
  outcome_note: string;
  answered_material_id?: string | null;
  started_at?: string | null;
  closed_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface MemoirCompletionIssueCollection {
  project_id: string;
  current_issue?: MemoirCompletionIssue | null;
  issues: MemoirCompletionIssue[];
}

export interface MemoirCompletionIssueCloseRequest {
  outcome: Exclude<MemoirCompletionIssueOutcome, "answered">;
  note?: string;
}

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
  | "book_editorial_copy_generated";
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
  | {
      type: "session.start";
      guidance_mode: MemoirGuidanceMode;
      current_guidance_module?: string;
      speaker_role?: MemoirSpeakerRole;
      parent_message_id?: string;
      ai_model?: string;
    }
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

export type MemoirMaterialOfflineASRStatus = "idle" | "pending" | "running" | "succeeded" | "failed";

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
  transcription_status?: string;
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
  transcript_source: string;
  transcript_text: string;
  text_hash: string;
  duration_ms: number;
  alignment_strategy: string;
  alignment_confidence?: number | null;
  min_select_ms: number;
  max_select_ms: number;
  ai_background_enabled: boolean;
  default_background_source: string;
  default_background_style: string;
  default_background_mode: string;
  background_sources: MemoirShareVideoBackgroundSource[];
  background_styles: MemoirShareVideoBackgroundStyle[];
  background_modes: MemoirShareVideoBackgroundMode[];
  segments: MemoirTranscriptShareVideoSegment[];
}

export type MemoirShareVideoJobStatus = "pending" | "running" | "succeeded" | "failed";
export type MemoirImageAssetStatus = "pending" | "running" | "ready" | "failed" | "deleted";
export type MemoirImageAssetSourceType =
  | "share_video_background"
  | "chapter_illustration"
  | "utterance_illustration"
  | "cover_candidate"
  | "manual_upload";
export type MemoirBookImagePlacementType =
  | "chapter_opening"
  | "chapter_inline"
  | "appendix"
  | "cover"
  | "back_cover";

export interface MemoirShareVideoOutput {
  mime_type: string;
  size_bytes: number;
  download_url: string;
}

export interface MemoirShareVideoJob {
  id: string;
  project_id: string;
  utterance_id: string;
  audio_file_id: string;
  status: MemoirShareVideoJobStatus;
  selected_text: string;
  start_char: number;
  end_char: number;
  start_ms: number;
  end_ms: number;
  duration_ms: number;
  background_source?: string;
  background_style?: string;
  background_mode?: string;
  background_effective_mode?: string;
  custom_background_asset_id?: string | null;
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

export interface MemoirShareVideoCustomBackgroundAsset {
  id: string;
  project_id: string;
  status: MemoirImageAssetStatus;
  preview_url: string;
  mime_type: string;
  size_bytes?: number | null;
  width?: number | null;
  height?: number | null;
  processing_preset: string;
  original_mime_type: string;
  original_size_bytes?: number | null;
}

export interface MemoirBookImagePlacement {
  id: string;
  project_id: string;
  image_asset_id: string;
  placement_type: MemoirBookImagePlacementType;
  chapter_id?: string | null;
  chapter_title: string;
  caption_override: string;
  display_size: string;
  sort_order: number;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface MemoirImageAsset {
  id: string;
  project_id: string;
  status: MemoirImageAssetStatus;
  source_type: MemoirImageAssetSourceType;
  source_signature: string;
  storage_path: string;
  thumbnail_storage_path: string;
  file_url: string;
  thumbnail_url: string;
  mime_type: string;
  size_bytes?: number | null;
  width?: number | null;
  height?: number | null;
  title: string;
  caption: string;
  alt_text: string;
  style_id: string;
  prompt_hash: string;
  prompt_version: string;
  provider: string;
  model: string;
  source_utterance_id?: string | null;
  source_chapter_id?: string | null;
  source_chapter_title: string;
  share_video_job_id?: string | null;
  source_quote: string;
  metadata_json: Record<string, unknown>;
  include_in_book: boolean;
  placements: MemoirBookImagePlacement[];
  created_at: string;
  updated_at: string;
}

export interface MemoirImageAssetListResponse {
  items: MemoirImageAsset[];
  total: number;
  ready_count: number;
  included_count: number;
  unused_count: number;
}

export interface MemoirImageAssetUpdateRequest {
  title?: string | null;
  caption?: string | null;
  alt_text?: string | null;
}

export interface MemoirBookImagePlacementCreateRequest {
  placement_type?: MemoirBookImagePlacementType;
  chapter_id?: string | null;
  caption_override?: string | null;
  display_size?: "full_width" | "half_width" | "inline";
}

export interface MemoirVoiceSearchTranscriptionResponse {
  transcript: string;
}

export interface MemoirVoiceSearchQueryFilters {
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

export interface MemoirBookChapterSourceRef {
  voice_session_id: string;
  message_id: string;
  utterance_id: string;
  transcript_version_id?: string | null;
  audio_file_id?: string | null;
  start_ms?: number | null;
  end_ms?: number | null;
  created_at?: string | null;
  quote: string;
}

export type MemoirSectionType =
  | "event"
  | "life_fragment"
  | "person_portrait"
  | "life_stage_summary"
  | "viewpoint_reflection";

export type MemoirSectionStatus = "active" | "archived";

export interface MemoirChapterSectionMaterialSummary {
  utterance_id: string;
  sort_order: number;
  text: string;
  include_in_book: boolean;
}

export interface MemoirChapterSectionSummary {
  id: string;
  title: string;
  section_type: MemoirSectionType;
  sort_order: number;
  status: MemoirSectionStatus;
  include_in_book: boolean;
  requires_user_assignment: boolean;
  version: number;
  material_count: number;
  included_material_count: number;
  materials: MemoirChapterSectionMaterialSummary[];
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

export interface MemoirBookPlanChapterInput {
  title: string;
  include_in_book: boolean;
  source_refs: MemoirBookChapterSourceRef[];
}

export interface MemoirBookPlanChapterUpdateInput extends MemoirBookPlanChapterInput {
  id?: string;
}

export interface MemoirBookPlanChapter {
  id: string;
  plan_id: string;
  project_id: string;
  title: string;
  sort_order: number;
  include_in_book: boolean;
  source_refs: MemoirBookChapterSourceRef[];
  sections: MemoirChapterSectionSummary[];
  metadata_json: Record<string, unknown>;
  pending_source_count: number;
  pending_completion_source_count: number;
  pending_new_source_count: number;
  removed_source_count: number;
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

export interface MemoirBookPlanGenerateRequest {
  ai_model?: string;
  max_chapters?: number;
}

export interface MemoirBookChapterTitleUpdateRequest {
  title: string;
}

export interface MemoirBookChapterTitleGenerateRequest {
  ai_model?: string;
}

export interface MemoirBookPlanConfirmRequest {
  title?: string;
  chapters: MemoirBookPlanChapterInput[];
}

export interface MemoirBookPlanUpdateRequest {
  title?: string;
  chapters: MemoirBookPlanChapterUpdateInput[];
}

export type MemoirBookPlanStructureCommandType =
  | "delete_chapter"
  | "merge_chapters"
  | "move_chapter"
  | "rename_chapter"
  | "set_chapter_book_inclusion"
  | "split_chapter";

export interface MemoirBookPlanStructureCommandRequest {
  type: MemoirBookPlanStructureCommandType;
  chapter_id?: string;
  target_chapter_id?: string;
  direction?: "up" | "down";
  target_position?: "first" | "last";
  target_index?: number;
  relative_chapter_id?: string;
  relative_position?: "before" | "after";
  title?: string;
  include_in_book?: boolean;
  utterance_ids?: string[];
}

export interface MemoirBookChapterDraftVersion {
  id: string;
  draft_id: string;
  project_id: string;
  chapter_id: string;
  version: number;
  content: string;
  sections: MemoirChapterDraftSection[];
  created_by: MemoirCreatedBy;
  model?: string | null;
  prompt_snapshot: string;
  feedback?: string | null;
  created_at: string;
}

export interface MemoirChapterDraftSection {
  section_id: string;
  title: string;
  section_type: MemoirSectionType;
  section_version: number;
  sort_order: number;
  content: string;
  used_material_ids: string[];
}

export interface MemoirBookChapterDraft {
  id: string;
  project_id: string;
  chapter_id: string;
  status: MemoirBookChapterDraftStatus;
  metadata_json: Record<string, unknown>;
  pending_source_count: number;
  removed_source_count: number;
  latest_version?: MemoirBookChapterDraftVersion | null;
  versions: MemoirBookChapterDraftVersion[];
  created_at: string;
  updated_at: string;
}

export interface MemoirBookChapterDraftRevisionRequest {
  expected_version_id: string;
  sections: Array<{
    section_id: string;
    content: string;
  }>;
}

export interface MemoirBookAssemblyResponse {
  project_id: string;
  output_id: string;
  output_version_id: string;
  version: number;
  content: string;
  chapter_snapshot_json: Array<Record<string, unknown>>;
  created_at: string;
}

export interface MemoirBookAudioRequest {
  voice?: string;
  speed_ratio?: number;
  force_regenerate?: boolean;
}

export interface MemoirBookAudioResponse {
  audio_file_ids: string[];
  audio_file_durations_seconds?: Array<number | null>;
  provider: string;
  model: string;
  mime_type: string;
  chunk_count: number;
  source_text_hash: string;
  duration_seconds?: number | null;
  alignment_provider?: string | null;
  alignment?: TTSAlignmentSegment[];
}

export type MemoirBookCoverStyle = "classic_plain" | "warm_gray" | "dark_album" | "moonlit_courtyard" | "navy_gilt";

export interface MemoirBookOutputUpdateRequest {
  title?: string | null;
  subtitle?: string | null;
  narrator_name?: string | null;
  prepared_by?: string | null;
  preface?: string | null;
  afterword?: string | null;
  back_cover_quote?: string | null;
  cover_style?: MemoirBookCoverStyle | null;
}

export type MemoirBookEditorialCopyTarget =
  | "preface"
  | "afterword"
  | "back_cover_quote"
  | "chapter_intros"
  | "pull_quotes";

export interface MemoirBookOutputEditorialCopyRegenerateRequest {
  ai_model?: string | null;
  targets?: MemoirBookEditorialCopyTarget[];
  chapter_ids?: string[];
  feedback?: string | null;
}

export interface MemoirBookTitleSuggestionRequest {
  ai_model?: string | null;
  count?: number;
}

export interface MemoirBookTitleSuggestionResponse {
  suggestions: string[];
  provider?: string | null;
  model?: string | null;
}

export type MemoirBookOutputExportFormat = "pdf" | "txt";
export type MemoirBookOutputExportLayout = "standard" | "large_print";

export interface MemoirBookOutputSectionTableOfContentsItem {
  section_id: string;
  index: number;
  title: string;
  char_count: number;
  source_count: number;
}

export interface MemoirBookOutputSection extends MemoirBookOutputSectionTableOfContentsItem {
  section_type: string;
  content: string;
}

export interface MemoirBookOutputTableOfContentsItem {
  chapter_id: string;
  index: number;
  title: string;
  char_count: number;
  source_count: number;
  sections: MemoirBookOutputSectionTableOfContentsItem[];
}

export interface MemoirBookOutputStructuredChapter {
  chapter_id: string;
  index: number;
  title: string;
  content: string;
  char_count: number;
  source_count: number;
  draft_version_id?: string | null;
  draft_version?: number | null;
  sections: MemoirBookOutputSection[];
}

export interface MemoirBookOutputStructuredContent {
  title: string;
  chapter_count: number;
  char_count: number;
  table_of_contents: MemoirBookOutputTableOfContentsItem[];
  chapters: MemoirBookOutputStructuredChapter[];
}

export type MemoirBookManuscriptTemplate = "classic_family" | "photo_memory" | "oral_archive";

export interface MemoirBookManuscriptProfile {
  template: MemoirBookManuscriptTemplate;
  title: string;
  subtitle: string;
  narrator_name: string;
  prepared_by: string;
  interview_date_range: string;
  privacy_note: string;
  ai_disclosure_note: string;
}

export interface MemoirBookManuscriptSection {
  kind: string;
  title: string;
  content: string;
  enabled: boolean;
}

export interface MemoirBookManuscriptQuote {
  text: string;
  source_ref_ids: string[];
  source?: "original_quote" | "editorial_generated" | "manual";
}

export interface MemoirBookManuscriptPersonNote {
  name: string;
  note: string;
}

export interface MemoirBookManuscriptUnconfirmedFact {
  text: string;
  source_ref_ids: string[];
}

export interface MemoirBookManuscriptSourceRef {
  utterance_id: string;
  transcript_version_id?: string | null;
  voice_session_id?: string | null;
  quote: string;
}

export interface MemoirBookManuscriptIllustration {
  image_asset_id: string;
  storage_path: string;
  mime_type: string;
  caption: string;
  alt_text: string;
  placement: MemoirBookImagePlacementType;
  display_size: string;
  source_quote: string;
  disclosure: string;
}

export interface MemoirBookManuscriptChapter {
  chapter_id: string;
  index: number;
  title: string;
  subtitle: string;
  intro: string;
  time_hint: string;
  place_hint: string;
  pull_quotes: MemoirBookManuscriptQuote[];
  people_notes: MemoirBookManuscriptPersonNote[];
  uncertain_facts: MemoirBookManuscriptUnconfirmedFact[];
  content: string;
  sections: MemoirBookOutputSection[];
  source_refs: MemoirBookManuscriptSourceRef[];
  illustrations: MemoirBookManuscriptIllustration[];
}

export interface MemoirBookManuscriptRenderSettings {
  page_size: "a5";
  layout: "reading" | "print";
  font_scale: "standard" | "large";
  show_headers: boolean;
  show_source_appendix: boolean;
  photo_mode: "none" | "chapter" | "appendix";
  cover_style: MemoirBookCoverStyle;
  back_cover_quote: string;
  editorial_copy_model?: string | null;
}

export interface MemoirBookManuscriptBackMatter {
  afterword: MemoirBookManuscriptSection;
  interview_records: MemoirBookManuscriptSection;
  version_note: MemoirBookManuscriptSection;
  unconfirmed_facts: MemoirBookManuscriptSection;
  source_appendix: MemoirBookManuscriptSection;
}

export interface MemoirBookManuscript {
  schema_version: number;
  profile: MemoirBookManuscriptProfile;
  front_matter: MemoirBookManuscriptSection[];
  table_of_contents: MemoirBookOutputTableOfContentsItem[];
  chapters: MemoirBookManuscriptChapter[];
  image_appendix: MemoirBookManuscriptIllustration[];
  back_matter: MemoirBookManuscriptBackMatter;
  render_settings: MemoirBookManuscriptRenderSettings;
}

export interface MemoirBookOutputVersion {
  id: string;
  output_id: string;
  project_id: string;
  plan_id: string;
  version: number;
  content: string;
  chapter_snapshot_json: Record<string, unknown>[];
  structured_content?: MemoirBookOutputStructuredContent | null;
  manuscript?: MemoirBookManuscript | null;
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
  is_stale: boolean;
  stale_chapter_titles: string[];
  latest_version?: MemoirBookOutputVersion | null;
  versions: MemoirBookOutputVersion[];
  created_at: string;
  updated_at: string;
}
