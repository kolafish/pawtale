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
  | "continue_confirmation"
  | "start_interview"
  | "confirm_fact"
  | "review_draft"
  | "complete";

export interface MemoirChapterCompletionNextAction {
  kind: MemoirChapterCompletionNextActionKind;
  title: string;
  detail: string;
  action_text: string;
  disabled: boolean;
  task_id?: string | null;
  confirmation_id?: string | null;
}

export interface MemoirChapterCompletionTask {
  id: string;
  question: string;
  status: "pending" | "in_progress" | "deferred";
  active_session_id?: string | null;
}

export interface MemoirChapterCompletionConfirmation {
  id: string;
  question: string;
  detail: string;
}

export interface MemoirChapterCompletionSummary {
  chapter_id: string;
  title: string;
  sort_order: number;
  state: MemoirChapterCompletionState;
  next_action: MemoirChapterCompletionNextAction;
  active_task?: MemoirChapterCompletionTask | null;
  confirmation?: MemoirChapterCompletionConfirmation | null;
  headline: string;
  detail: string;
}

export interface MemoirBookCompletionOverview {
  project_id: string;
  headline: string;
  detail: string;
  total_chapters: number;
  completed_chapters: number;
  chapters: MemoirChapterCompletionSummary[];
}
