export type ProjectType = "novel" | "article" | "memoir";
export type MemoirSessionStatus = "active" | "completed";
export type MemoirQuestionStatus = "pending" | "answered" | "skipped";
export type MemoirAnswerStatus = "uploaded" | "transcribing" | "answered" | "drafted" | "confirmed";
export type MemoirDraftStatus = "draft" | "accepted";

export interface User {
  id: string;
  email: string;
  display_name: string;
  avatar_url?: string;
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

export interface MemoirInterviewState {
  session: MemoirSession;
  current_question?: MemoirQuestion;
  completed_question_count: number;
  pending_question_count: number;
  latest_draft?: MemoirDraft;
  questions: MemoirQuestion[];
}
