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
  type: string;
  name: string;
  sort_order: number;
}

export interface ReferenceItem {
  id: string;
  project_id: string;
  type: string;  // character/location/note
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
  type: string; // location / note
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
