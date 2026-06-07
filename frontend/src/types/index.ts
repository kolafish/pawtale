import type { ReferenceType, SettingReferenceType } from '../domain/references';

export type { ReferenceType, SettingReferenceType } from '../domain/references';

export interface User {
  id: string;
  email: string;
  display_name: string;
  avatar_url?: string;
  created_at: string;
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

export interface ProjectExportResponse {
  title: string;
  sections: { group: string; documents: { name: string; content: string; word_count: number }[] }[];
  total_words: number;
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
