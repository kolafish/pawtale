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
  | "start_interview"
  | "confirm"
  | "review_draft"
  | "complete";

export interface MemoirChapterCompletionNextAction {
  kind: MemoirChapterCompletionNextActionKind;
  title: string;
  detail: string;
  action_text: string;
  disabled: boolean;
  item_id?: string | null;
}

export interface MemoirChapterCompletionItem {
  id: string;
  question: string;
  status: "pending" | "in_progress" | "deferred";
  active_session_id?: string | null;
}

export interface MemoirChapterCompletionSummary {
  chapter_id: string;
  title: string;
  sort_order: number;
  state: MemoirChapterCompletionState;
  next_action: MemoirChapterCompletionNextAction;
  active_item?: MemoirChapterCompletionItem | null;
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
