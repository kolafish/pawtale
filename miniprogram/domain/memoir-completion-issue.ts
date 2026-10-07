export type MemoirCompletionIssueStatus =
  | "open"
  | "in_progress"
  | "closed";
export type MemoirCompletionIssueOutcome =
  | "answered"
  | "resolved_by_material"
  | "confirmed"
  | "not_remembered"
  | "deferred"
  | "declined"
  | "superseded";
export type MemoirCompletionIssueScope = "chapter" | "book";
export type MemoirCompletionIssueChannel = "interview" | "manual_review";
export type MemoirCompletionIssueType =
  | "context"
  | "sequence"
  | "relationship"
  | "motivation"
  | "impact"
  | "meaning"
  | "clarity"
  | "structure"
  | "privacy"
  | "conflict";
export type MemoirCompletionIssuePriority = "high" | "medium" | "low";

export interface MemoirCompletionIssue {
  id: string;
  project_id: string;
  scope: MemoirCompletionIssueScope;
  chapter_id?: string | null;
  issue_type: MemoirCompletionIssueType;
  channel: MemoirCompletionIssueChannel;
  question: string;
  reason: string;
  priority: MemoirCompletionIssuePriority;
  related_material_ids: string[];
  input_signature: string;
  status: MemoirCompletionIssueStatus;
  blocks_book: boolean;
  active_session_id?: string | null;
  outcome?: MemoirCompletionIssueOutcome | null;
  outcome_note: string;
  answered_material_id?: string | null;
  started_at?: string | null;
  closed_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface MemoirCompletionIssueCollection {
  project_id: string;
  issues: MemoirCompletionIssue[];
}

export interface MemoirCompletionIssueCloseRequest {
  outcome: Exclude<MemoirCompletionIssueOutcome, "answered">;
  note?: string;
}
