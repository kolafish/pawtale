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
}
