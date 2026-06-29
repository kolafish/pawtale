export type MemoirGuidanceMode = "free_narration" | "guided_interview";
export type MemoirGuidanceModule = "person" | "event" | "place" | "period" | "theme";
export type MemoirVoiceTranscriptIncludedFilter = "all" | "included" | "not_included";
export type MemoirVoiceSessionStatus = "created" | "listening" | "speaking" | "stopped" | "failed";
export type MemoirChatMessageRole = "user" | "assistant" | "system";
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

export interface MemoirVoiceSession {
  id: string;
  project_id: string;
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

export type MemoirAutomationEventType =
  | "source_assigned"
  | "source_held"
  | "chapter_auto_revised"
  | "audit_completed";
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

export interface MemoirProjectProgressChapter {
  chapter_id: string;
  title: string;
  sort_order: number;
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
  segment_final?: boolean;
  turn_final?: boolean;
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
  is_included_in_book?: boolean;
  included_chapters?: MemoirBookSourceIncludedChapter[];
  created_at: string;
  session_created_at: string;
}

export interface MemoirVoiceTranscriptListResponse {
  items: MemoirVoiceTranscriptItem[];
  total: number;
  limit: number;
  offset: number;
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
}

export interface MemoirBookPlanChapterInput {
  title: string;
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
  utterance_id?: string;
  utterance_ids?: string[];
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

export interface MemoirBookVoiceAssistantTTSRequest extends MemoirBookAudioRequest {
  text: string;
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

export interface MemoirBookOutputGenerateRequest {
  ai_model?: string | null;
  title?: string | null;
  subtitle?: string | null;
  narrator_name?: string | null;
  prepared_by?: string | null;
  preface?: string | null;
  afterword?: string | null;
  back_cover_quote?: string | null;
  cover_style?: MemoirBookCoverStyle | null;
  force_regenerate?: boolean;
}

export type MemoirBookCoverStyle = "classic_plain" | "warm_gray" | "dark_album";

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
