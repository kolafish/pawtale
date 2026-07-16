export type MemoirChapterCompletionState =
  | "ready"
  | "in_progress"
  | "preparing"
  | "needs_preparation"
  | "needs_review"
  | "complete"
  | "failed";

export type MemoirChapterCompletionNextActionKind =
  | "create_direction"
  | "confirm_direction"
  | "refresh_audit"
  | "wait"
  | "continue_confirmation"
  | "start_interview"
  | "open_editorial_fix"
  | "open_manual_review"
  | "generate_draft"
  | "review_draft"
  | "complete";

export interface MemoirChapterCompletionNextAction {
  kind: MemoirChapterCompletionNextActionKind;
  eyebrow: string;
  title: string;
  detail: string;
  action_text: string;
  disabled: boolean;
  task_id?: string | null;
}

export interface MemoirChapterCompletionTask {
  id: string;
  gap_id: string;
  question: string;
  purpose: string;
  known_context_summary: string;
  priority: number;
  status: "pending" | "in_progress" | "deferred";
  active_session_id?: string | null;
}

export interface MemoirChapterCompletionGap {
  id: string;
  title: string;
  description: string;
  recommended_action: "interview" | "editorial" | "manual_review";
}

export interface MemoirChapterCompletionSummary {
  chapter_id: string;
  title: string;
  sort_order: number;
  state: MemoirChapterCompletionState;
  audit_status: "not_started" | "pending" | "running" | "succeeded" | "failed" | "skipped";
  audit_is_current: boolean;
  next_action: MemoirChapterCompletionNextAction;
  must_handle_count: number;
  suggested_count: number;
  accepted_incomplete_count: number;
  active_task?: MemoirChapterCompletionTask | null;
  visible_tasks: MemoirChapterCompletionTask[];
  editorial_gaps: MemoirChapterCompletionGap[];
  review_gaps: MemoirChapterCompletionGap[];
  draft_readiness: "blocked" | "ready_to_generate" | "needs_review" | "ready";
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
