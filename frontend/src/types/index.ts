import type { ReferenceType, SettingReferenceType } from '../domain/references';

export type { ReferenceType, SettingReferenceType } from '../domain/references';

export interface User {
  id: string;
  email: string;
  display_name: string;
  avatar_url?: string;
  role: string;
  created_at: string;
}

// -- User audit types -----------------------------------------------------

export type AuditPlatform = 'miniprogram' | 'web';

export interface AuditUserBrief {
  id: string;
  email: string;
  display_name: string;
  role: string;
}

export interface AuditDashboardFilters {
  from: string;
  to: string;
  user_id?: string;
  platform?: AuditPlatform;
  feature_code?: string;
}

export interface AuditDashboardSummary {
  active_ms: number;
  session_count: number;
  event_count: number;
  user_count: number;
  top_exit_page_path?: string | null;
  top_exit_feature_code?: string | null;
}

export interface AuditDailyUserUsage {
  user: AuditUserBrief;
  date: string;
  platform: string;
  active_ms: number;
  session_count: number;
  event_count: number;
  last_exit_page_path: string;
  last_exit_feature_code: string;
  last_exit_reason: string;
  last_seen_at?: string | null;
}

export interface AuditDailyFeatureUsage {
  user: AuditUserBrief;
  date: string;
  platform: string;
  feature_code: string;
  active_ms: number;
  event_count: number;
  last_seen_at?: string | null;
}

export interface AuditExitLocation {
  page_path: string;
  feature_code: string;
  reason: string;
  count: number;
}

export interface AuditDashboardResponse {
  summary: AuditDashboardSummary;
  daily_users: AuditDailyUserUsage[];
  feature_usage: AuditDailyFeatureUsage[];
  exit_locations: AuditExitLocation[];
}

export interface AuditEvent {
  id: string;
  client_event_id: string;
  event_type: string;
  event_date: string;
  platform: string;
  page_path: string;
  feature_code: string;
  project_id?: string | null;
  voice_session_id?: string | null;
  occurred_at: string;
  duration_ms: number;
  active_ms: number;
  exit_reason: string;
  metadata_json: Record<string, unknown>;
}

export interface AuditSession {
  id: string;
  client_session_id: string;
  platform: string;
  started_at: string;
  last_seen_at: string;
  ended_at?: string | null;
  active_ms: number;
  exit_page_path: string;
  exit_feature_code: string;
  exit_reason: string;
  app_version: string;
  device_summary: string;
  events: AuditEvent[];
}

export interface AuditUserDayResponse {
  user: AuditUserBrief;
  date: string;
  sessions: AuditSession[];
}

export interface AIDebugInfo {
  request: {
    provider: string;
    model: string;
    temperature?: number;
    max_tokens?: number;
    system_prompt?: string;
    user_prompt?: string;
  };
  response: {
    raw_text?: string;
    raw_json?: Record<string, unknown> | unknown[] | string | null;
  };
  meta: {
    truncated: boolean;
    max_chars?: number;
    generated_at?: string;
  };
}

// ── Project-based writing types ──────────────────────

export type ProjectType = 'novel' | 'article' | 'memoir';

export interface Project {
  id: string;
  type: ProjectType;
  title: string;
  description?: string;
  word_count_target?: number;
  daily_word_goal?: number;
  total_word_count: number;
  groups: DocumentGroup[];
  references: ReferenceItemBrief[];
  created_at: string;
  updated_at: string;
}

export interface ProjectListItem {
  id: string;
  type: ProjectType;
  title: string;
  description?: string;
  word_count_target?: number;
  total_word_count: number;
  group_count: number;
  document_count: number;
  updated_at: string;
}

export interface DocumentGroup {
  id: string;
  name: string;
  sort_order: number;
  documents: DocumentMeta[];
}

export interface DocumentMeta {
  id: string;
  group_id: string;
  name: string;
  status: string;
  label?: string;
  word_count: number;
  sort_order: number;
  updated_at: string;
}

export interface WritingDocument {
  id: string;
  group_id: string;
  name: string;
  content: string;
  synopsis?: string;
  status: string;
  label?: string;
  word_count_target?: number;
  word_count: number;
  sort_order: number;
  created_at: string;
  updated_at: string;
}

export interface ReferenceItemBrief {
  id: string;
  type: ReferenceType;
  name: string;
  sort_order: number;
}

export interface ReferenceItem {
  id: string;
  project_id: string;
  type: ReferenceType;
  name: string;
  content: string;
  metadata_json: Record<string, unknown>;
  sort_order: number;
  created_at: string;
  updated_at: string;
}

export interface WritingSession {
  id: string;
  project_id: string;
  date: string;
  words_written: number;
  created_at: string;
}

export type ProjectExportDownloadFormat = 'txt' | 'markdown' | 'zip';

export interface ProjectExportResponse {
  title: string;
  sections: { group: string; documents: { name: string; content: string; word_count: number }[] }[];
  total_words: number;
  references?: {
    id: string;
    type: ReferenceType;
    name: string;
    content: string;
    metadata_json: Record<string, unknown>;
    sort_order: number;
    updated_at?: string | null;
  }[];
}

export interface ProjectExportDownload {
  blob: Blob;
  filename?: string;
}

export interface GitSyncInstallation {
  installation_id: number;
  account_login?: string | null;
  account_type?: string | null;
  repository_selection?: string | null;
}

export interface GitHubSetupUrl {
  install_url: string;
  expires_at: string;
}

export interface GitSyncRepositoryOption {
  installation_id: number;
  owner: string;
  name: string;
  full_name: string;
  private: boolean;
  default_branch: string;
}

export interface ProjectGitSyncConfig {
  project_id: string;
  installation_id: number;
  repository_owner: string;
  repository_name: string;
  branch: string;
  base_path: string;
  last_synced_at?: string | null;
  last_commit_sha?: string | null;
  last_error?: string | null;
}

export interface GitSyncStatus {
  configured: boolean;
  install_url?: string | null;
  installations: GitSyncInstallation[];
  repositories: GitSyncRepositoryOption[];
  project_config?: ProjectGitSyncConfig | null;
}

export interface ProjectGitSyncRunResponse {
  ok: boolean;
  synced_files: number;
  commit_sha?: string | null;
  synced_at: string;
  error?: string | null;
}

// ── Memoir review surfaces ─────────────────────────

export type MemoirMemoryStatus = 'draft' | 'confirmed' | 'rejected';
export type MemoirMemoryType = 'person' | 'event' | 'place' | 'theme' | 'open_question';
export type MemoirOpenQuestionStatus = 'open' | 'asked' | 'answered' | 'dismissed';
export type MemoirCoverageStatus = 'red' | 'yellow' | 'green';
export type MemoirQuestionType = 'clarification' | 'depth' | 'scene' | 'person' | 'emotion' | 'connection' | 'closure';
export type MemoirSensitivity = 'low' | 'medium' | 'high';

export interface MemoirSessionResponse {
  id: string;
  project_id: string;
  title: string;
  status: 'active' | 'completed';
  current_question_id?: string | null;
  created_at: string;
  updated_at: string;
}

export interface MemoirQuestionResponse {
  id: string;
  session_id: string;
  prompt: string;
  source: 'template' | 'ai_generated';
  sort_order: number;
  status: 'pending' | 'answered' | 'skipped';
  created_at: string;
}

export interface MemoirInterviewState {
  session: MemoirSessionResponse;
  current_question?: MemoirQuestionResponse | null;
  completed_question_count: number;
  pending_question_count: number;
  questions: MemoirQuestionResponse[];
}

export interface MemoirPersonMemory {
  id: string;
  project_id: string;
  canonical_name: string;
  aliases_json: string[];
  relationship?: string | null;
  description: string;
  confidence?: number | null;
  status: MemoirMemoryStatus;
}

export interface MemoirEventMemory {
  id: string;
  project_id: string;
  title: string;
  life_stage?: string | null;
  time_text?: string | null;
  place_text?: string | null;
  process: string;
  emotion?: string | null;
  importance?: number | null;
  confidence?: number | null;
  status: MemoirMemoryStatus;
}

export interface MemoirPlaceMemory {
  id: string;
  project_id: string;
  name: string;
  elder_alias?: string | null;
  modern_name?: string | null;
  place_type?: string | null;
  description: string;
  confidence?: number | null;
  status: MemoirMemoryStatus;
}

export interface MemoirThemeMemory {
  id: string;
  project_id: string;
  name: string;
  description: string;
  strength?: number | null;
  mention_count: number;
  confidence?: number | null;
  status: MemoirMemoryStatus;
}

export interface MemoirOpenQuestion {
  id: string;
  project_id: string;
  question: string;
  question_type: MemoirQuestionType;
  reason: string;
  sensitivity: MemoirSensitivity;
  priority?: number | null;
  status: MemoirOpenQuestionStatus;
}

export interface MemoirCoverageItem {
  topic_id: string;
  key: string;
  name: string;
  life_stage?: string | null;
  answered_count: number;
  memory_count: number;
  open_question_count: number;
  coverage_score: number;
  status: MemoirCoverageStatus;
}

export interface MemoirQuestionCandidate {
  id: string;
  project_id: string;
  question: string;
  question_type: MemoirQuestionType;
  reason: string;
  sensitivity: MemoirSensitivity;
  priority: number;
  status: 'candidate' | 'selected' | 'asked' | 'dismissed';
}

export interface MemoirSessionSummary {
  id: string;
  project_id: string;
  session_id: string;
  summary: string;
  new_people_json: Record<string, unknown>[];
  new_events_json: Record<string, unknown>[];
  new_places_json: Record<string, unknown>[];
  new_themes_json: Record<string, unknown>[];
  open_questions_json: Record<string, unknown>[];
  next_question_suggestions_json: Record<string, unknown>[];
}

export interface MemoirOutput {
  id: string;
  project_id: string;
  output_type: 'session_summary' | 'story_fragment' | 'timeline' | 'confirmation_todo';
  title: string;
  source_session_id?: string | null;
  status: 'draft' | 'accepted' | 'archived';
  updated_at: string;
}

// ── AI Revision / Diff ────────────────────────────

export interface DiffChunk {
  id: string;
  type: 'equal' | 'insert' | 'delete';
  text: string;
  accepted: boolean | null; // null=pending, true=accepted, false=rejected
}

export interface SelectionRange {
  start: number;
  end: number;
  text: string;
}

export interface MultiReviseResultItem {
  ai_model: string;
  revised_text: string | null;
  error: string | null;
  debug_info?: AIDebugInfo;
}

export interface MultiReviseResponse {
  document_id: string;
  original_text: string;
  scope: string;
  results: MultiReviseResultItem[];
}

// ── Project Analysis (AI extract from manuscript) ──

export interface AnalyzedCharacter {
  name: string;
  gender?: string | null;
  identity?: string | null;
  personality?: string[];
  appearance?: string | null;
  relationships?: string | null;
  summary?: string | null;
}

export interface AnalyzedOutline {
  name: string;
  content: string;
}

export interface AnalyzedSetting {
  name: string;
  type: SettingReferenceType;
  content: string;
}

export interface ProjectAnalyzeResponse {
  characters: AnalyzedCharacter[];
  outlines: AnalyzedOutline[];
  settings: AnalyzedSetting[];
  total_words_analyzed: number;
  debug_info?: AIDebugInfo;
}

export interface AnalyzeApplyRequest {
  characters: AnalyzedCharacter[];
  outlines: AnalyzedOutline[];
  settings: AnalyzedSetting[];
}

export interface AnalyzeApplyResponse {
  created_count: number;
  references: ReferenceItemBrief[];
}

// ── Memoir interview editor ───────────────────────

export type MemoirVoiceSessionStatus = 'created' | 'listening' | 'speaking' | 'stopped' | 'failed';
export type MemoirGuidanceMode = 'free_narration' | 'guided_interview';
export type MemoirInterviewType = 'ai_interview' | 'human_interview';
export type MemoirChatMessageRole = 'user' | 'assistant' | 'interviewer' | 'system';
export type MemoirBookPlanStatus = 'draft' | 'confirmed';

export interface MemoirBookSourceIncludedChapter {
  chapter_id: string;
  title: string;
  sort_order: number;
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

export interface MemoirChapterSourceEditorChapter {
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

export interface MemoirChapterSourceEditorSource {
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
  source_material_type: string;
  source_route_action: string;
  source_route_confidence?: number | null;
  source_route_reason: string;
  source_route_updated_at?: string | null;
  is_included: boolean;
  included_in_chapters: MemoirBookSourceIncludedChapter[];
  current_chapter_id?: string | null;
  current_chapter_title?: string | null;
  current_chapter_sort_order?: number | null;
}

export interface MemoirChapterSourceEditor {
  project_id: string;
  plan_id?: string | null;
  plan_title?: string | null;
  plan_status?: MemoirBookPlanStatus | null;
  total_source_count: number;
  covered_source_count: number;
  uncovered_source_count: number;
  chapters: MemoirChapterSourceEditorChapter[];
  sources: MemoirChapterSourceEditorSource[];
  uncovered_sources: MemoirChapterSourceEditorSource[];
}

export interface MemoirInterviewEditorAnswer {
  id: string;
  message_id: string;
  parent_question_message_id?: string | null;
  voice_session_id: string;
  project_id: string;
  audio_file_id?: string | null;
  audio_mime_type?: string | null;
  audio_size_bytes?: number | null;
  audio_duration_seconds?: number | null;
  transcript_version_id?: string | null;
  transcript_raw: string;
  transcript_edited: string;
  display_text: string;
  start_ms?: number | null;
  end_ms?: number | null;
  provider?: string | null;
  source_material_type: string;
  source_route_action: string;
  source_route_confidence?: number | null;
  source_route_reason: string;
  source_route_updated_at?: string | null;
  is_included_in_book: boolean;
  included_chapters: MemoirBookSourceIncludedChapter[];
  turn_index: number;
  created_at: string;
}

export interface MemoirInterviewEditorQuestion {
  id: string;
  voice_session_id: string;
  project_id: string;
  role: MemoirChatMessageRole;
  text: string;
  audio_file_id?: string | null;
  transcript_version_id?: string | null;
  utterance_id?: string | null;
  answer_count: number;
  answers: MemoirInterviewEditorAnswer[];
  turn_index: number;
  created_at: string;
  updated_at: string;
}

export interface MemoirInterviewEditorTimelineItem {
  message_id: string;
  voice_session_id: string;
  project_id: string;
  role: MemoirChatMessageRole;
  parent_message_id?: string | null;
  text: string;
  audio_file_id?: string | null;
  transcript_version_id?: string | null;
  utterance_ids: string[];
  source_material_type: string;
  source_route_action: string;
  turn_index: number;
  created_at: string;
  updated_at: string;
}

export interface MemoirInterviewEditorSessionSummary {
  id: string;
  project_id: string;
  status: MemoirVoiceSessionStatus;
  guidance_mode: MemoirGuidanceMode;
  interview_type: MemoirInterviewType;
  current_guidance_module?: string | null;
  started_at?: string | null;
  stopped_at?: string | null;
  created_at: string;
  updated_at: string;
  question_count: number;
  answer_count: number;
  unassigned_answer_count: number;
  total_audio_seconds: number;
  last_message_text: string;
  last_message_role?: MemoirChatMessageRole | null;
  last_message_at?: string | null;
}

export interface MemoirInterviewEditorSessionList {
  items: MemoirInterviewEditorSessionSummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface MemoirInterviewEditorSessionDetail {
  session: MemoirInterviewEditorSessionSummary;
  questions: MemoirInterviewEditorQuestion[];
  unassigned_answers: MemoirInterviewEditorAnswer[];
  raw_timeline: MemoirInterviewEditorTimelineItem[];
}
