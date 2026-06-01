export interface User {
  id: string;
  email: string;
  display_name: string;
  avatar_url?: string;
  created_at: string;
}

export interface Character {
  id: string;
  name: string;
  gender?: string;
  age?: number;
  identity?: string;
  appearance?: string;
  personality: string[];
  speaking_style?: string;
  background?: string;
  inspiration?: string;
  avatar_url?: string;
  base_image_url?: string;
  is_preset: boolean;
  created_at: string;
  // 新增深度字段
  role_type?: string; // protagonist/supporting/antagonist
  core_trait?: string; // 底色 - 核心性格驱动力
  life_goal?: string; // 目标追求
  fatal_flaw?: string; // 致命弱点
  growth_hint?: string; // 成长弧线提示
  habits: string[]; // 行为习惯
  secret?: string; // 秘密
}

export interface CharacterAutoComplete {
  gender?: string;
  age?: number;
  identity?: string;
  appearance?: string;
  personality: string[];
  speaking_style?: string;
  background?: string;
  // 新增深度字段
  core_trait?: string;
  life_goal?: string;
  fatal_flaw?: string;
  growth_hint?: string;
  habits: string[];
  secret?: string;
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

export interface Scene {
  id: string;
  name: string;
  category: string;
  description: string;
  location?: string;
  time_weather?: string;
  mood_keywords: string[];
  is_preset: boolean;
  created_at: string;
  // 新增增强字段
  dramatic_potential?: string; // 戏剧潜力
  sensory_details: { visual?: string; auditory?: string; olfactory?: string };
  props: string[]; // 可能的道具/元素
}

// AI 场景生成
export interface SceneAIGenerateRequest {
  category: string;
  mood_keywords: string[];
  character_context?: { character_ids: string[]; relationship?: string };
  custom_prompt?: string;
  count: number;
  ai_model?: string;
  debug?: boolean;
}

export interface SceneCandidate {
  name: string;
  description: string;
  location?: string;
  time_weather?: string;
  mood_keywords: string[];
  dramatic_potential?: string;
  sensory_details: { visual?: string; auditory?: string; olfactory?: string };
  props: string[];
}

export interface SceneAIGenerateResponse {
  candidates: SceneCandidate[];
  debug_info?: AIDebugInfo;
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
  global_id?: string;
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

// ── Export to Global Library ──────────────────────

export interface ExportGlobalRequest {
  action: 'preview' | 'create' | 'update';
  global_id?: string;
}

export interface ExportGlobalMatch {
  global_id: string;
  global_name: string;
  global_type: string;
}

export interface ExportGlobalResponse {
  status: 'preview' | 'created' | 'updated' | 'no_match';
  match?: ExportGlobalMatch;
  global_id?: string;
}

// ── Import Preview ────────────────────────────────

export interface ImportPreviewItem {
  global_id: string;
  global_name: string;
  global_type: string;
  existing_ref_id?: string;
  existing_ref_name?: string;
  match_by?: string;
}

export interface ImportPreviewResponse {
  items: ImportPreviewItem[];
}
