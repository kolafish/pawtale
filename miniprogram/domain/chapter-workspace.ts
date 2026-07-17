import type { MemoirChapterCompletionSummary } from "./chapter-completion";


export type MemoirEditingJobStatus =
  | "pending"
  | "running"
  | "succeeded"
  | "failed"
  | "skipped";

export interface MemoirChapterPreparationJob {
  id: string;
  chapter_id: string;
  status: MemoirEditingJobStatus;
  created_at: string;
}

export interface MemoirChapterWorkspace {
  project_id: string;
  chapter_id: string;
  chapter_title: string;
  completion_summary: MemoirChapterCompletionSummary;
  draft_id?: string | null;
  draft_version_id?: string | null;
  draft_version?: number | null;
  draft_status?: string | null;
  draft_content: string;
}
