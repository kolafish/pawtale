export type MemoirChapterCompletionState =
  | "not_started"
  | "preparing"
  | "needs_input"
  | "draft_ready"
  | "confirmed"
  | "failed";

export type MemoirChapterCompletionNextActionKind =
  | "prepare"
  | "wait"
  | "review_sections"
  | "confirm"
  | "review_draft"
  | "complete";

export interface MemoirChapterCompletionNextAction {
  kind: MemoirChapterCompletionNextActionKind;
  title: string;
  detail: string;
  action_text: string;
  disabled: boolean;
  issue_id?: string | null;
}

export interface MemoirChapterCompletionIssue {
  id: string;
  question: string;
  status: "pending" | "in_progress";
}

export interface MemoirChapterCompletionSummary {
  chapter_id: string;
  title: string;
  sort_order: number;
  state: MemoirChapterCompletionState;
  next_action: MemoirChapterCompletionNextAction;
  active_issue?: MemoirChapterCompletionIssue | null;
  blocking_issue_count: number;
  headline: string;
  detail: string;
}

export interface MemoirBookCompletionOverview {
  project_id: string;
  headline: string;
  detail: string;
  total_chapters: number;
  completed_chapters: number;
  blocking_issue_count: number;
  chapters: MemoirChapterCompletionSummary[];
}
