export type ProjectType = "novel" | "article" | "memoir";
export type MemoirSessionStatus = "active" | "completed";
export type MemoirQuestionStatus = "pending" | "answered" | "skipped";
export type MemoirAnswerStatus = "uploaded" | "transcribing" | "answered" | "drafted" | "confirmed";
export type MemoirDraftStatus = "draft" | "accepted";
export type MemoirMemoryStatus = "draft" | "confirmed" | "rejected";
export type MemoirMemoryType = "person" | "event" | "place" | "theme" | "open_question";
export type MemoirQuestionType = "clarification" | "depth" | "scene" | "person" | "emotion" | "connection" | "closure";
export type MemoirSensitivity = "low" | "medium" | "high";
export type MemoirOpenQuestionStatus = "open" | "asked" | "answered" | "dismissed";
export type MemoirCoverageStatus = "red" | "yellow" | "green";
export type MemoirQuestionCandidateStatus = "candidate" | "selected" | "asked" | "dismissed";

export interface User {
  id: string;
  email: string;
  display_name: string;
  avatar_url?: string;
  storage_owner_id?: string;
  created_at: string;
}

export interface Project {
  id: string;
  type: ProjectType;
  title: string;
  description?: string;
  total_word_count: number;
  groups: Array<{
    id: string;
    name: string;
    sort_order: number;
    documents: Array<{
      id: string;
      group_id: string;
      name: string;
      status: string;
      word_count: number;
      sort_order: number;
      updated_at: string;
    }>;
  }>;
  references: Array<{
    id: string;
    type: string;
    name: string;
    sort_order: number;
  }>;
  created_at: string;
  updated_at: string;
}

export interface MemoirSession {
  id: string;
  project_id: string;
  title: string;
  status: MemoirSessionStatus;
  current_question_id?: string;
  created_at: string;
  updated_at: string;
}

export interface MemoirQuestion {
  id: string;
  session_id: string;
  prompt: string;
  source: "template" | "ai_generated";
  sort_order: number;
  status: MemoirQuestionStatus;
  created_at: string;
}

export interface MemoirAnswer {
  id: string;
  question_id: string;
  audio_file_id?: string;
  transcript_raw: string;
  transcript_edited: string;
  status: MemoirAnswerStatus;
  duration_seconds?: number;
  created_at: string;
  updated_at: string;
}

export interface MemoirAudioFile {
  id: string;
  mime_type: string;
  size_bytes: number;
  duration_seconds?: number;
  created_at: string;
}

export interface MemoirAudioTranscription {
  audio_file_id: string;
  text: string;
  usage?: Record<string, unknown>;
}

export interface MemoirDraft {
  id: string;
  answer_id: string;
  content_raw: string;
  content_edited: string;
  status: MemoirDraftStatus;
  created_at: string;
  updated_at: string;
}

export interface MemoirStoryCard {
  id: string;
  answer_id: string;
  question_id: string;
  session_id: string;
  session_title: string;
  question_prompt: string;
  answer_preview: string;
  answer_status: MemoirAnswerStatus;
  draft_id?: string;
  draft_status?: MemoirDraftStatus;
  draft_preview?: string;
  duration_seconds?: number;
  created_at: string;
  updated_at: string;
}

export interface MemoirStoryList {
  stories: MemoirStoryCard[];
  total: number;
}

export interface MemoirStoryDetail {
  story: MemoirStoryCard;
  question: MemoirQuestion;
  answer: MemoirAnswer;
  draft?: MemoirDraft;
}

export interface MemoirSummary {
  total_story_count: number;
  weekly_story_count: number;
  pending_review_count: number;
  confirmed_story_count: number;
  total_question_count: number;
  completed_question_count: number;
  progress_percent: number;
}

export interface MemoirPersonMemory {
  id: string;
  project_id: string;
  canonical_name: string;
  aliases_json: string[];
  relationship?: string | null;
  first_seen_session_id?: string | null;
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
  cause: string;
  process: string;
  result: string;
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
  session_id?: string | null;
  target_memory_type?: string | null;
  target_memory_id?: string | null;
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
  description: string;
  sort_order: number;
  answered_count: number;
  memory_count: number;
  open_question_count: number;
  coverage_score: number;
  status: MemoirCoverageStatus;
  signals_json?: Record<string, unknown>;
  calculated_at: string;
}

export interface MemoirQuestionCandidate {
  id: string;
  project_id: string;
  session_id?: string | null;
  question: string;
  question_type: MemoirQuestionType;
  target_memory_type?: string | null;
  target_memory_id?: string | null;
  coverage_topic_id?: string | null;
  reason: string;
  sensitivity: MemoirSensitivity;
  stage: string;
  priority: number;
  status: MemoirQuestionCandidateStatus;
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
  output_type: "session_summary" | "story_fragment" | "timeline" | "confirmation_todo";
  title: string;
  source_session_id?: string | null;
  status: "draft" | "accepted" | "archived";
  updated_at: string;
  content_preview?: string;
  latest_version?: number | null;
  latest_version_created_at?: string | null;
  version_count?: number;
}

export interface MemoirMemoryStats {
  total_memory_count: number;
  draft_memory_count: number;
  confirmed_memory_count: number;
  rejected_memory_count: number;
  open_question_count: number;
  asked_open_question_count: number;
  answered_open_question_count: number;
  dismissed_open_question_count: number;
  question_candidate_count: number;
  output_count: number;
  coverage_average: number;
  low_coverage_count: number;
}

export interface MemoirDashboardResponse {
  project: Project;
  interview: MemoirInterviewState;
  summary: MemoirSummary;
  session_summary?: MemoirSessionSummary | null;
  people: MemoirPersonMemory[];
  events: MemoirEventMemory[];
  places: MemoirPlaceMemory[];
  themes: MemoirThemeMemory[];
  open_questions: MemoirOpenQuestion[];
  coverage: MemoirCoverageItem[];
  candidates: MemoirQuestionCandidate[];
  outputs: MemoirOutput[];
  memory_stats: MemoirMemoryStats;
}

export interface MemoirSessionProcessResponse {
  session_summary: MemoirSessionSummary;
  story_fragment?: MemoirOutput | null;
  people: MemoirPersonMemory[];
  events: MemoirEventMemory[];
  places: MemoirPlaceMemory[];
  themes: MemoirThemeMemory[];
  open_questions: MemoirOpenQuestion[];
}

export interface MemoirInterviewState {
  session: MemoirSession;
  current_question?: MemoirQuestion;
  completed_question_count: number;
  pending_question_count: number;
  latest_draft?: MemoirDraft;
  questions: MemoirQuestion[];
}
