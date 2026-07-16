export type MemoirNarrativeGapStatus =
  | "open"
  | "resolved"
  | "accepted_incomplete"
  | "not_applicable"
  | "dismissed";

export type MemoirNarrativeGapSeverity = "high" | "medium" | "low";
export type MemoirNarrativeGapRecommendedAction =
  | "interview"
  | "editorial"
  | "manual_review";
export type MemoirNarrativeGapActionReasonCode =
  | "missing_new_evidence"
  | "existing_material_available"
  | "fact_conflict_detected"
  | "publication_decision_required"
  | "draft_usage_problem"
  | "migration_review_required";
export type MemoirNarrativeGapActionDecisionSource =
  | "deterministic_policy"
  | "migration"
  | "manual_override";
export type MemoirEditingJobStatus = "pending" | "running" | "succeeded" | "failed" | "skipped";

export interface MemoirChapterWorkspaceEvidence {
  id: string;
  evidence_role: string;
  evidence_source_type: string;
  utterance_id: string | null;
  transcript_version_id: string | null;
  audio_file_id: string | null;
  image_asset_id: string | null;
  start_ms: number | null;
  end_ms: number | null;
  quote: string | null;
  evidence_note: string | null;
  created_at: string;
}

export interface MemoirChapterWorkspaceClaim {
  id: string;
  statement: string;
  claim_type: string;
  time_text: string | null;
  certainty: string;
  verification_status: string;
  publication_status: string;
  evidence: MemoirChapterWorkspaceEvidence[];
}

export interface MemoirChapterWorkspaceStorySlot {
  id: string | null;
  slot_key: string;
  required: boolean;
  status: string;
  assessment_summary: string;
  claim_ids: string[];
}

export interface MemoirChapterWorkspaceStory {
  id: string;
  title: string;
  story_type: string;
  summary: string;
  completion_status: string;
  verification_status: string;
  publication_status: string;
  story_version: number;
  slots: MemoirChapterWorkspaceStorySlot[];
  claims: MemoirChapterWorkspaceClaim[];
}

export interface MemoirChapterWorkspaceAvailableStory {
  id: string;
  title: string;
  story_type: string;
  summary: string;
  completion_status: string;
  verification_status: string;
  story_version: number;
  excluded_from_chapter: boolean;
}

export interface MemoirChapterWorkspaceNodeStory {
  id: string;
  story_id: string;
  story_role: string;
  sort_order: number;
  required: boolean;
}

export interface MemoirChapterWorkspaceNode {
  id: string;
  node_type: string;
  title: string;
  purpose: string;
  sort_order: number;
  required: boolean;
  status: string;
  stories: MemoirChapterWorkspaceNodeStory[];
}

export interface MemoirChapterWorkspaceContract {
  id: string;
  version: number;
  objective: string;
  reader_takeaway: string;
  time_scope_text: string;
  time_start: string | null;
  time_end: string | null;
  core_questions: Array<{ key: string; question: string }>;
  constraints: {
    include_story_ids: string[];
    exclude_story_ids: string[];
    include_topics: string[];
    exclude_topics: string[];
  };
  status: string;
  nodes: MemoirChapterWorkspaceNode[];
}

export interface MemoirChapterContractRevisionRequest {
  idempotency_key: string;
  objective: string;
  reader_takeaway: string;
  time_scope_text: string;
  time_start: string | null;
  time_end: string | null;
  core_questions: Array<{ key: string; question: string }>;
  constraints: MemoirChapterWorkspaceContract["constraints"];
  copy_from_contract_id: string;
  copy_nodes: boolean;
}

export interface MemoirChapterStoryDecisionRequest {
  idempotency_key: string;
  reason?: string;
}

export interface MemoirNarrativeGap {
  id: string;
  project_id: string;
  scope_type: string;
  primary_story_id: string | null;
  primary_chapter_id: string | null;
  gap_type: string;
  severity: MemoirNarrativeGapSeverity;
  target_slot: string;
  title: string;
  description: string;
  fingerprint: string;
  status: MemoirNarrativeGapStatus;
  recommended_action: MemoirNarrativeGapRecommendedAction;
  action_reason_code: MemoirNarrativeGapActionReasonCode;
  action_decision_source: MemoirNarrativeGapActionDecisionSource;
  action_policy_version: string;
  detected_by: string;
  detection_count: number;
  resolution_note: string;
  resolved_by: string | null;
  resolved_by_user_id: string | null;
  resolved_at: string | null;
  first_detected_at: string;
  last_detected_at: string;
  claims: Array<{ claim_id: string; role: string }>;
  stories: Array<{ story_id: string; role: string }>;
  chapters: Array<{ chapter_id: string; role: string }>;
  created_at: string;
  updated_at: string;
}

export interface MemoirChapterWorkspaceGapGroups {
  must_handle: MemoirNarrativeGap[];
  suggested: MemoirNarrativeGap[];
  accepted_incomplete: MemoirNarrativeGap[];
}

export interface MemoirChapterGapAuditJob {
  id: string;
  project_id: string;
  chapter_id: string | null;
  status: MemoirEditingJobStatus;
  result_json: Record<string, unknown>;
  error_message: string;
  created_at: string;
  updated_at: string;
}

export interface MemoirChapterWorkspaceAnswerUpdate {
  id: string;
  task_id: string;
  status: "applied" | "needs_confirmation";
  question: string;
  answer_text: string;
  summaries: string[];
  created_at: string;
}

export interface MemoirChapterWorkspace {
  project_id: string;
  chapter_id: string;
  chapter_title: string;
  contract: MemoirChapterWorkspaceContract | null;
  stories: MemoirChapterWorkspaceStory[];
  available_stories: MemoirChapterWorkspaceAvailableStory[];
  answer_updates: MemoirChapterWorkspaceAnswerUpdate[];
  gaps: MemoirChapterWorkspaceGapGroups;
  latest_audit_job: MemoirChapterGapAuditJob | null;
}

export interface MemoirChapterGapAuditRequest {
  include_semantic_audit?: boolean;
  ai_model?: string | null;
}

export interface MemoirNarrativeGapResolutionRequest {
  idempotency_key: string;
  note?: string;
}

export type MemoirNarrativeGapAction =
  | "resolve"
  | "accept-incomplete"
  | "not-applicable"
  | "dismiss";
