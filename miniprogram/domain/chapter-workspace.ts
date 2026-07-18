import type { MemoirChapterCompletionSummary } from "./chapter-completion";

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
