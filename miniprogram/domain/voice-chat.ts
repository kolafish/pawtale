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
export type MemoirFollowupTaskStatus =
  | "pending"
  | "in_progress"
  | "answered"
  | "deferred"
  | "unable_to_answer"
  | "closed_without_answer"
  | "cancelled";
export type MemoirFollowupTaskClaimRole = "known" | "do_not_repeat" | "expected_evidence";
export type MemoirFollowupTaskUtteranceRole = "target_answer" | "related_context";
export type MemoirFollowupUnableToAnswerResolution = "unable_to_answer" | "ask_family_later";
export type MemoirNarrativeActorType = "ai" | "user" | "system";
export type MemoirBookCompletionState =
  | "ready"
  | "in_progress"
  | "preparing"
  | "needs_preparation"
  | "needs_review"
  | "complete"
  | "failed";
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
  task_id?: string | null;
  chapter_id?: string | null;
}

export interface MemoirBookCompletionChapter {
  chapter_id: string;
  title: string;
  sort_order: number;
  state: MemoirBookCompletionState;
  headline: string;
  detail: string;
  focus_areas: string[];
  task_id?: string | null;
  active_session_id?: string | null;
}

export interface MemoirBookCompletionOverview {
  project_id: string;
  headline: string;
  detail: string;
  total_chapters: number;
  completed_chapters: number;
  chapters: MemoirBookCompletionChapter[];
}

export interface MemoirFollowupTaskClaim {
  claim_id: string;
  context_role: MemoirFollowupTaskClaimRole;
}

export interface MemoirFollowupTaskUtterance {
  utterance_id: string;
  result_role: MemoirFollowupTaskUtteranceRole;
  created_at: string;
}

export interface MemoirFollowupTask {
  id: string;
  project_id: string;
  gap_id: string;
  chapter_id?: string | null;
  story_id?: string | null;
  target_slot: string;
  question: string;
  purpose: string;
  known_context_summary: string;
  priority: number;
  status: MemoirFollowupTaskStatus;
  assigned_session_id?: string | null;
  defer_until?: string | null;
  defer_reason: string;
  created_by: MemoirNarrativeActorType;
  started_at?: string | null;
  completed_at?: string | null;
  claims: MemoirFollowupTaskClaim[];
  utterances: MemoirFollowupTaskUtterance[];
  created_at: string;
  updated_at: string;
}

export interface MemoirFollowupTaskListResponse {
  items: MemoirFollowupTask[];
  total: number;
}

export interface MemoirFollowupTaskActionRequest {
  idempotency_key: string;
  note?: string;
}

export interface MemoirFollowupTaskDeferRequest extends MemoirFollowupTaskActionRequest {
  defer_until?: string | null;
}

export interface MemoirFollowupTaskSwitchRequest extends MemoirFollowupTaskActionRequest {
  current_task_id: string;
}

export interface MemoirVoiceSession {
  id: string;
  project_id: string;
  status: MemoirVoiceSessionStatus;
  guidance_mode: MemoirGuidanceMode;
  interview_type: MemoirInterviewType;
  session_goal: MemoirSessionGoal;
  current_followup_task_id?: string | null;
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
  interview_type: MemoirInterviewType;
  session_goal: MemoirSessionGoal;
  current_followup_task_id?: string | null;
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
  | "source_assigned"
  | "source_held"
  | "chapter_auto_revised"
  | "audit_completed"
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
  utterance_ids: string[];
  summary_text: string;
  confidence?: number | null;
  metadata_json: Record<string, unknown>;
  utterance_details?: MemoirAutomationEventUtteranceDetail[];
  created_at: string;
  updated_at: string;
}

export interface MemoirAutomationEventUtteranceDetail {
  utterance_id: string;
  source_index?: number | null;
  record_title: string;
  preview_text: string;
  chapter_id?: string | null;
  chapter_title?: string | null;
  created_at?: string | null;
}

export interface MemoirAutomationEventListResponse {
  items: MemoirAutomationEvent[];
  total: number;
  limit: number;
}

export type MemoirFollowupAnswerKind =
  | "direct_narrative"
  | "clarification"
  | "fact_resolution"
  | "mixed";

export interface MemoirFollowupAnswerInterpretation {
  task_id: string;
  status: "pending" | "needs_confirmation" | "resolved";
  confirmation_type: "answer_usage" | "fact_resolution";
  answer_kind?: MemoirFollowupAnswerKind | null;
  direct_narrative_text: string;
  clarification_text: string;
  fact_resolution_text: string;
  confirmation_question: string;
  reason: string;
}

export interface MemoirCompletionOutcome {
  status: "processing" | "incorporated" | "context_recorded" | "needs_confirmation" | "not_used";
  voice_session_id: string;
  task_id?: string | null;
  task_status?: MemoirFollowupTask["status"] | null;
  chapter_id?: string | null;
  chapter_title: string;
  utterance_id?: string | null;
  answer_kind?: MemoirFollowupAnswerKind | null;
  route_action: string;
  reason: string;
  message: string;
  review_event_id?: string | null;
}

export interface MemoirBookSourceReviewItem {
  id: string;
  project_id: string;
  status: MemoirAutomationEventStatus;
  utterance_id: string;
  source_index: number;
  route_action: "assign_to_chapter" | "record_as_context" | "suggest_new_chapter" | "hold_for_review" | "ignore";
  target_chapter_id?: string | null;
  target_chapter_title?: string | null;
  suggested_title?: string | null;
  reason: string;
  summary_text: string;
  source_text: string;
  followup_answer?: MemoirFollowupAnswerInterpretation | null;
  created_at: string;
  updated_at: string;
}

export interface MemoirBookSourceReviewListResponse {
  items: MemoirBookSourceReviewItem[];
  total: number;
}

export interface MemoirBookSourceReviewConfirmRequest {
  followup_answer_kind: MemoirFollowupAnswerKind;
  direct_narrative_text?: string;
  clarification_text?: string;
  fact_resolution_text?: string;
}

export interface MemoirBookSourceReviewConfirmResponse {
  item: MemoirBookSourceReviewItem;
  plan: MemoirBookPlan;
}

export interface MemoirProjectProgressChapter {
  chapter_id: string;
  title: string;
  sort_order: number;
  include_in_book: boolean;
  source_count: number;
  pending_source_count: number;
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
  current_followup_task_id?: string;
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
  reason: string;
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

export interface MemoirBookSourceIncludedChapter {
  chapter_id: string;
  title: string;
  sort_order: number;
  include_in_book?: boolean;
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
  metadata_json: Record<string, unknown>;
  pending_source_count: number;
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
  is_included?: boolean;
  included_in_chapters?: MemoirBookSourceIncludedChapter[];
}

export interface MemoirBookSourceCoverage {
  project_id: string;
  plan_id?: string | null;
  total_source_count: number;
  covered_source_count: number;
  uncovered_source_count: number;
  sources?: MemoirBookSourceCoverageItem[];
  uncovered_sources: MemoirBookSourceCoverageItem[];
}

export interface MemoirBookSourceAssignmentInput {
  chapter_id: string;
  utterance_ids: string[];
}

export interface MemoirBookSourceAssignmentRequest {
  ai_model?: string;
  assignments: MemoirBookSourceAssignmentInput[];
}

export interface MemoirBookSourceMoveRequest {
  ai_model?: string;
  source_chapter_id: string;
  target_chapter_id: string;
  utterance_id: string;
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
  | "create_chapter_from_source"
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
  utterance_id?: string;
  utterance_ids?: string[];
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
  pending_source_count: number;
  removed_source_count: number;
  latest_version?: MemoirBookChapterDraftVersion | null;
  versions: MemoirBookChapterDraftVersion[];
  created_at: string;
  updated_at: string;
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

export type MemoirBookVoiceCommandIntent =
  | "read_outline"
  | "read_chapter"
  | "read_uncovered_source"
  | "rename_chapter"
  | "merge_chapters"
  | "move_chapter"
  | "assign_source"
  | "split_chapter"
  | "rewrite_chapter"
  | "polish_chapter"
  | "confirm_chapter"
  | "review_chapter_title"
  | "review_chapter_length"
  | "review_chapter_structure"
  | "answer_book_question"
  | "capability_help"
  | "clarify_previous"
  | "unknown";

export type MemoirBookVoiceCommandResponseKind = "action_preview" | "consultation" | "answer" | "clarification";

export interface MemoirBookVoiceCommandContextMessage {
  role: "user" | "assistant";
  text: string;
  intent?: string | null;
  summary?: string | null;
}

export interface MemoirBookVoiceCommandBookContext {
  source_coverage?: {
    total_source_count: number;
    covered_source_count: number;
    uncovered_source_count: number;
  };
  uncovered_sources?: Array<{
    source_index: number;
    utterance_id: string;
    text: string;
  }>;
}

export interface MemoirBookVoiceCommandParseRequest {
  text: string;
  current_chapter_id?: string;
  conversation_context?: MemoirBookVoiceCommandContextMessage[];
  book_context?: MemoirBookVoiceCommandBookContext;
  ai_model?: string;
}

export interface MemoirBookVoiceCommandOperation {
  type: MemoirBookVoiceCommandIntent;
  chapter_id?: string | null;
  chapter_title?: string | null;
  target_chapter_id?: string | null;
  target_chapter_title?: string | null;
  new_title?: string | null;
  direction?: "up" | "down" | string | null;
  target_position?: "first" | "last" | string | null;
  target_index?: number | null;
  relative_chapter_id?: string | null;
  relative_chapter_title?: string | null;
  relative_position?: "before" | "after" | string | null;
  source_position?: number | null;
  source_index?: number | null;
  utterance_id?: string | null;
}

export interface MemoirBookVoiceCommandParseResponse {
  original_text: string;
  intent: MemoirBookVoiceCommandIntent;
  intent_label: string;
  response_kind: MemoirBookVoiceCommandResponseKind;
  summary: string;
  reply_text?: string | null;
  operations: MemoirBookVoiceCommandOperation[];
  requires_confirmation: boolean;
  used_ai: boolean;
  provider?: string | null;
  model?: string | null;
}

export interface MemoirBookVoiceCommandExecuteRequest {
  intent: MemoirBookVoiceCommandIntent;
  operation: MemoirBookVoiceCommandOperation;
}

export interface MemoirBookVoiceCommandExecuteResponse {
  intent: MemoirBookVoiceCommandIntent;
  summary: string;
  plan: MemoirBookPlan;
  source_coverage?: MemoirBookSourceCoverage | null;
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

export interface MemoirBookOutputTableOfContentsItem {
  chapter_id: string;
  index: number;
  title: string;
  char_count: number;
  source_count: number;
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
  formal_version_id?: string | null;
  formal_audit_job_id?: string | null;
  formalized_by_user_id?: string | null;
  formalized_at?: string | null;
  metadata_json: Record<string, unknown>;
  is_stale: boolean;
  stale_chapter_titles: string[];
  latest_version?: MemoirBookOutputVersion | null;
  versions: MemoirBookOutputVersion[];
  created_at: string;
  updated_at: string;
}

export interface MemoirBookConsistencyAuditRequest {
  include_semantic_audit?: boolean;
  ai_model?: string | null;
}

export type MemoirEditingJobStatus = "pending" | "running" | "succeeded" | "failed" | "skipped";

export interface MemoirBookConsistencyAuditJob {
  id: string;
  project_id: string;
  status: MemoirEditingJobStatus;
  payload_json: Record<string, unknown>;
  result_json: Record<string, unknown>;
  error_message: string;
  created_at: string;
  updated_at: string;
}

export interface MemoirBookReadinessBlocker {
  code: string;
  title: string;
  detail: string;
  chapter_id?: string | null;
  story_id?: string | null;
  claim_id?: string | null;
  draft_block_id?: string | null;
  gap_id?: string | null;
}

export interface MemoirBookReadiness {
  project_id: string;
  ready_for_formal: boolean;
  current_signature: string;
  output_status?: MemoirOutputStatus | null;
  output_version_id?: string | null;
  latest_audit_job?: MemoirBookConsistencyAuditJob | null;
  audit_is_current: boolean;
  groups: {
    must_handle: MemoirBookReadinessBlocker[];
    suggested: MemoirBookReadinessBlocker[];
    accepted_incomplete: MemoirBookReadinessBlocker[];
  };
}

export interface MemoirBookFormalizeResponse {
  project_id: string;
  output_id: string;
  output_version_id: string;
  audit_job_id: string;
  status: MemoirOutputStatus;
  formalized_at: string;
}
