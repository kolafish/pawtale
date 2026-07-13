import type { MemoirEditingJobStatus } from "./chapter-workspace";


export type MemoirDraftBlockRole =
  | "factual"
  | "transition"
  | "reflection"
  | "opening"
  | "closing"
  | "legacy_snapshot";

export type MemoirDraftBlockEditorialStatus = "draft" | "confirmed" | "excluded";
export type MemoirDraftBlockFreshnessStatus = "current" | "stale";

export interface MemoirDraftBlockClaimSource {
  claim_id: string;
  usage_role: "core" | "context" | "detail" | "quoted";
}

export interface MemoirDraftBlockStorySource {
  story_id: string;
  story_revision_id: string;
  usage_role: "primary" | "supporting" | "contrast";
}

export interface MemoirDraftBlockVersion {
  id: string;
  project_id: string;
  block_id: string;
  version: number;
  content: string;
  created_by: "ai" | "user" | "system";
  model: string | null;
  prompt_snapshot: string;
  feedback: string | null;
  legacy_draft_version_id: string | null;
  claims: MemoirDraftBlockClaimSource[];
  stories: MemoirDraftBlockStorySource[];
  created_at: string;
}

export interface MemoirDraftBlock {
  id: string;
  project_id: string;
  chapter_id: string;
  node_id: string | null;
  block_role: MemoirDraftBlockRole;
  sort_order: number;
  editorial_status: MemoirDraftBlockEditorialStatus;
  freshness_status: MemoirDraftBlockFreshnessStatus;
  locked: boolean;
  current_version_id: string;
  legacy_draft_id: string | null;
  current_version: MemoirDraftBlockVersion;
  version_count: number;
  created_at: string;
  updated_at: string;
}

export interface MemoirDraftBlockListResponse {
  chapter_id: string;
  blocks: MemoirDraftBlock[];
}

export interface MemoirDraftBlockJob {
  id: string;
  project_id: string;
  chapter_id: string | null;
  job_type: "generate_draft_blocks";
  status: MemoirEditingJobStatus;
  result_json: Record<string, unknown>;
  error_message: string;
  created_at: string;
  updated_at: string;
}

export interface MemoirDraftBlockGenerationRequest {
  node_ids?: string[];
  ai_model?: string | null;
}

export interface MemoirDraftBlockManualRevisionRequest {
  expected_current_version_id: string;
  content: string;
  feedback?: string | null;
}

export interface MemoirDraftBlockDecisionRequest {
  expected_current_version_id: string;
  idempotency_key: string;
  reason?: string;
}

export interface MemoirDraftBlockRevisionJobRequest {
  expected_current_version_id: string;
  feedback: string;
  ai_model?: string | null;
}

export interface MemoirDraftBlockRevisionApplyRequest {
  expected_current_version_id: string;
  generation_job_id: string;
}

export interface MemoirChapterDraftBlockAssembly {
  project_id: string;
  chapter_id: string;
  draft_id: string;
  draft_version_id: string;
  version: number;
  content: string;
  blocks: Array<{
    block_id: string;
    block_version_id: string;
    node_id: string;
    block_role: MemoirDraftBlockRole;
    node_sort_order: number;
    block_sort_order: number;
    editorial_status: MemoirDraftBlockEditorialStatus;
    freshness_status: MemoirDraftBlockFreshnessStatus;
    locked: boolean;
    content_char_count: number;
  }>;
  created_at: string;
}
