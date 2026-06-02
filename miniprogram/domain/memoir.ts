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

export interface MemoirDraft {
  id: string;
  answer_id: string;
  content_raw: string;
  content_edited: string;
  status: MemoirDraftStatus;
  created_at: string;
  updated_at: string;
}

export interface MemoirInterviewState {
  session: MemoirSession;
  current_question?: MemoirQuestion;
  completed_question_count: number;
  pending_question_count: number;
  latest_draft?: MemoirDraft;
  questions: MemoirQuestion[];
}
