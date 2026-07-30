import type { MemoirBookCompletionOverview } from "../domain/chapter-completion";
import type { MemoirBookManuscript } from "../domain/memoir-book";
import {
  memoirNarrativeProjectPath,
  memoirAIRequest,
  memoirRequest,
} from "./memoir-api-client";


function bookCompletionPath(projectId: string, suffix = ""): string {
  return memoirNarrativeProjectPath(projectId, `/book-completion${suffix}`);
}

function bookReviewPath(projectId: string): string {
  return memoirNarrativeProjectPath(projectId, "/book-review");
}

export interface MemoirBookReview {
  id: string;
  project_id: string;
  input_signature: string;
  status: "ready" | "failed" | "stale";
  review: {
    summary: string;
    strengths: string[];
    structure_guidance: string[];
    editorial_actions: MemoirBookEditorialAction[];
  };
  error_message: string;
}

export type MemoirBookEditorialActionType =
  | "remove_repetition"
  | "improve_transition"
  | "reorder_chapters";

export interface MemoirBookEditorialAction {
  id: string;
  action_type: MemoirBookEditorialActionType;
  title: string;
  reason: string;
  chapter_ids: string[];
}

export interface MemoirBookReviewJob {
  job_id: string | null;
  project_id: string;
  status: "idle" | "pending" | "running" | "succeeded" | "failed" | "cancelled";
  stage:
    | ""
    | "queued"
    | "preparing_chapters"
    | "reviewing"
    | "saving"
    | "completed"
    | "failed"
    | "cancelled";
  total_chapter_count: number;
  processed_chapter_count: number;
  current_chapter_title: string;
  requested_ai_model: string | null;
  resolved_ai_model: string | null;
  review_id: string | null;
  can_cancel: boolean;
  can_retry: boolean;
  error_message: string;
}

export type MemoirBookRevisionJobSource =
  | "review_answers"
  | "editorial_actions";

export interface MemoirBookRevisionJob {
  job_id: string | null;
  project_id: string;
  status: "idle" | "pending" | "running" | "succeeded" | "failed" | "cancelled";
  stage:
    | ""
    | "queued"
    | "assembling"
    | "revising"
    | "saving"
    | "completed"
    | "failed"
    | "cancelled";
  answer_count?: number;
  unresolved_count?: number;
  changed_chapter_count?: number;
  summary?: string;
  selected_action_count?: number;
  proposal_id: string | null;
  proposal_status: MemoirBookRevisionProposalStatus | null;
  requested_ai_model: string | null;
  resolved_ai_model: string | null;
  already_applied?: boolean;
  can_apply?: boolean;
  can_cancel: boolean;
  can_retry: boolean;
  error_message: string;
}

export type MemoirBookRevisionProposalStatus =
  | "ready"
  | "applied"
  | "dismissed"
  | "stale";

export interface MemoirBookRevisionSectionComparison {
  section_id: string;
  title: string;
  before_content: string;
  after_content: string;
  before_char_count: number;
  after_char_count: number;
}

export interface MemoirBookRevisionChapterComparison {
  chapter_id: string;
  title: string;
  sections: MemoirBookRevisionSectionComparison[];
}

export interface MemoirBookRevisionPreviewVersion {
  version: number;
  manuscript?: MemoirBookManuscript | null;
}

export type MemoirBookRevisionPreview = "none" | "before" | "after" | "both";

export interface MemoirBookRevisionProposal {
  id: string;
  project_id: string;
  status: MemoirBookRevisionProposalStatus;
  summary: string;
  base_output_version_id: string;
  result_output_version_id: string | null;
  comparison: {
    changed_chapter_count: number;
    changed_section_count: number;
    before_char_count: number;
    after_char_count: number;
    char_count_delta: number;
    chapters: MemoirBookRevisionChapterComparison[];
    chapter_order_changed: boolean;
    before_chapter_order: Array<{ chapter_id: string; title: string }>;
    after_chapter_order: Array<{ chapter_id: string; title: string }>;
  };
  before: MemoirBookRevisionPreviewVersion;
  after: MemoirBookRevisionPreviewVersion;
  can_apply: boolean;
  can_dismiss: boolean;
  created_at: string;
  updated_at: string;
}

export interface MemoirChapterPreparationJob {
  job_id: string | null;
  project_id: string;
  chapter_id: string;
  operation: "prepare" | "rewrite";
  status: "idle" | "pending" | "running" | "succeeded" | "failed" | "cancelled";
  stage: string;
  total_section_count: number;
  checked_section_count: number;
  completed_section_count: number;
  resumed_section_count: number;
  current_section_id: string | null;
  current_section_title: string;
  outcome: string;
  requested_ai_model: string | null;
  resolved_ai_model: string | null;
  can_cancel: boolean;
  error_message: string;
}

export interface MemoirBookCompletionJobChapter {
  chapter_id: string;
  title: string;
  job_id: string | null;
  status: "pending" | "running" | "succeeded" | "failed" | "cancelled";
  error_message: string;
}

export interface MemoirBookCompletionJob {
  job_id: string | null;
  project_id: string;
  scope: "pending" | "all";
  status: "idle" | "pending" | "running" | "succeeded" | "failed" | "cancelled";
  total_chapter_count: number;
  processed_chapter_count: number;
  succeeded_chapter_count: number;
  failed_chapter_count: number;
  cancelled_chapter_count: number;
  current_chapter_id: string | null;
  current_chapter_title: string;
  requested_ai_model: string | null;
  chapters: MemoirBookCompletionJobChapter[];
  can_cancel: boolean;
  can_retry: boolean;
  error_message: string;
}

function chapterPreparationJobPath(
  projectId: string,
  chapterId: string,
  suffix = "",
): string {
  return bookCompletionPath(
    projectId,
    `/chapters/${encodeURIComponent(chapterId)}/jobs${suffix}`,
  );
}

export function getMemoirBookCompletionOverview(
  projectId: string,
): Promise<MemoirBookCompletionOverview> {
  return memoirRequest<MemoirBookCompletionOverview>(bookCompletionPath(projectId, "/overview"));
}

export function getMemoirBookReview(
  projectId: string,
): Promise<MemoirBookReview | null> {
  return memoirRequest<MemoirBookReview | null>(bookReviewPath(projectId));
}

export function generateMemoirBookReview(
  projectId: string,
  forceRegenerate: boolean,
): Promise<MemoirBookReview> {
  return memoirAIRequest<
    MemoirBookReview,
    { force_regenerate: boolean; ai_model: null }
  >(bookReviewPath(projectId), {
    force_regenerate: forceRegenerate,
    ai_model: null,
  });
}

export function enqueueMemoirBookReviewJob(
  projectId: string,
  forceRegenerate: boolean,
): Promise<MemoirBookReviewJob> {
  return memoirAIRequest<
    MemoirBookReviewJob,
    { force_regenerate: boolean; ai_model: null }
  >(
    `${bookReviewPath(projectId)}/jobs`,
    {
      force_regenerate: forceRegenerate,
      ai_model: null,
    },
    { timeoutProfile: "mutation" },
  );
}

export function getMemoirBookReviewJob(
  projectId: string,
): Promise<MemoirBookReviewJob> {
  return memoirRequest<MemoirBookReviewJob>(
    `${bookReviewPath(projectId)}/jobs/current`,
  );
}

export function cancelMemoirBookReviewJob(
  projectId: string,
  jobId: string,
): Promise<MemoirBookReviewJob> {
  return memoirRequest<MemoirBookReviewJob>(
    `${bookReviewPath(projectId)}/jobs/${encodeURIComponent(jobId)}`,
    { method: "DELETE" },
  );
}

function bookRevisionJobPath(
  projectId: string,
  source: MemoirBookRevisionJobSource,
  suffix = "",
): string {
  const segment = source === "review_answers"
    ? "answer-application"
    : "editorial-revision";
  return `${bookReviewPath(projectId)}/${segment}/jobs${suffix}`;
}

export function enqueueMemoirBookRevisionJob(
  projectId: string,
  source: MemoirBookRevisionJobSource,
  actionIds: string[] = [],
): Promise<MemoirBookRevisionJob> {
  const payload = source === "review_answers"
    ? { ai_model: null }
    : { action_ids: actionIds, ai_model: null };
  return memoirAIRequest<MemoirBookRevisionJob, typeof payload>(
    bookRevisionJobPath(projectId, source),
    payload,
    { timeoutProfile: "mutation" },
  );
}

export function getMemoirBookRevisionJob(
  projectId: string,
  source: MemoirBookRevisionJobSource,
): Promise<MemoirBookRevisionJob> {
  return memoirRequest<MemoirBookRevisionJob>(
    bookRevisionJobPath(projectId, source, "/current"),
  );
}

export function cancelMemoirBookRevisionJob(
  projectId: string,
  jobId: string,
  source: MemoirBookRevisionJobSource,
): Promise<MemoirBookRevisionJob> {
  return memoirRequest<MemoirBookRevisionJob>(
    bookRevisionJobPath(
      projectId,
      source,
      `/${encodeURIComponent(jobId)}`,
    ),
    { method: "DELETE" },
  );
}

function bookRevisionProposalPath(
  projectId: string,
  proposalId: string,
  suffix = "",
): string {
  return `${bookReviewPath(projectId)}/revision-proposals/${
    encodeURIComponent(proposalId)
  }${suffix}`;
}

export function getMemoirBookRevisionProposal(
  projectId: string,
  proposalId: string,
  preview: MemoirBookRevisionPreview = "none",
): Promise<MemoirBookRevisionProposal> {
  return memoirRequest<MemoirBookRevisionProposal>(
    `${bookRevisionProposalPath(projectId, proposalId)}?preview=${preview}`,
  );
}

export function enqueueMemoirBookRevisionFeedback(
  projectId: string,
  proposalId: string,
  feedback: string,
): Promise<MemoirBookRevisionJob> {
  return memoirAIRequest<
    MemoirBookRevisionJob,
    { feedback: string; ai_model: null }
  >(
    bookRevisionProposalPath(projectId, proposalId, "/feedback/jobs"),
    { feedback, ai_model: null },
    { timeoutProfile: "mutation" },
  );
}

export function getMemoirBookRevisionFeedbackJob(
  projectId: string,
  proposalId: string,
): Promise<MemoirBookRevisionJob> {
  return memoirRequest<MemoirBookRevisionJob>(
    bookRevisionProposalPath(projectId, proposalId, "/feedback/jobs/current"),
  );
}

export function applyMemoirBookRevisionProposal(
  projectId: string,
  proposalId: string,
): Promise<MemoirBookRevisionProposal> {
  return memoirRequest<MemoirBookRevisionProposal>(
    `${bookRevisionProposalPath(projectId, proposalId, "/apply")}?preview=none`,
    { method: "POST" },
  );
}

export function dismissMemoirBookRevisionProposal(
  projectId: string,
  proposalId: string,
): Promise<MemoirBookRevisionProposal> {
  return memoirRequest<MemoirBookRevisionProposal>(
    `${bookRevisionProposalPath(projectId, proposalId, "/dismiss")}?preview=none`,
    { method: "POST" },
  );
}

export function enqueueMemoirBookCompletionJob(
  projectId: string,
  retryJobId: string | null = null,
): Promise<MemoirBookCompletionJob> {
  return memoirAIRequest<
    MemoirBookCompletionJob,
    { scope: "pending"; retry_job_id: string | null; ai_model: null }
  >(
    bookCompletionPath(projectId, "/jobs"),
    { scope: "pending", retry_job_id: retryJobId, ai_model: null },
    { timeoutProfile: "mutation" },
  );
}

export function getMemoirBookCompletionJob(
  projectId: string,
): Promise<MemoirBookCompletionJob> {
  return memoirRequest<MemoirBookCompletionJob>(
    bookCompletionPath(projectId, "/jobs/current"),
  );
}

export function cancelMemoirBookCompletionJob(
  projectId: string,
  jobId: string,
): Promise<MemoirBookCompletionJob> {
  return memoirRequest<MemoirBookCompletionJob>(
    bookCompletionPath(
      projectId,
      `/jobs/${encodeURIComponent(jobId)}`,
    ),
    { method: "DELETE" },
  );
}

export function enqueueMemoirChapterPreparationJob(
  projectId: string,
  chapterId: string,
  operation: "prepare" | "rewrite",
): Promise<MemoirChapterPreparationJob> {
  return memoirAIRequest<
    MemoirChapterPreparationJob,
    { operation: "prepare" | "rewrite"; ai_model: null }
  >(
    chapterPreparationJobPath(projectId, chapterId),
    { operation, ai_model: null },
    { timeoutProfile: "mutation" },
  );
}

export function getMemoirChapterPreparationJob(
  projectId: string,
  chapterId: string,
): Promise<MemoirChapterPreparationJob> {
  return memoirRequest<MemoirChapterPreparationJob>(
    chapterPreparationJobPath(projectId, chapterId, "/current"),
  );
}

export function cancelMemoirChapterPreparationJob(
  projectId: string,
  chapterId: string,
  jobId: string,
): Promise<MemoirChapterPreparationJob> {
  return memoirRequest<MemoirChapterPreparationJob>(
    chapterPreparationJobPath(
      projectId,
      chapterId,
      `/${encodeURIComponent(jobId)}`,
    ),
    { method: "DELETE" },
  );
}
