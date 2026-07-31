import type { MemoirBookCompletionOverview } from "../domain/chapter-completion";
import type { MemoirBookManuscript } from "../domain/memoir-book";
import type { MemoirCompletionIssue } from "../domain/memoir-completion-issue";
import {
  memoirNarrativeProjectPath,
  memoirAIRequest,
  memoirRequest,
} from "./memoir-api-client";
import {
  cleanBaseUrl,
  readBackendConfig,
} from "./request";


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
  | "reorder_chapters"
  | "revise_book_profile";

export interface MemoirBookEditorialAction {
  id: string;
  action_type: MemoirBookEditorialActionType;
  title: string;
  reason: string;
  chapter_ids: string[];
  metadata_fields: MemoirBookRevisionMetadataField[];
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
  | "editorial_actions"
  | "proposal_feedback"
  | "proposal_draft";

export type MemoirBookRevisionCommandSource = Exclude<
  MemoirBookRevisionJobSource,
  "proposal_feedback" | "proposal_draft"
>;

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
  proposal_id: string | null;
  proposal_status: MemoirBookRevisionProposalStatus | null;
  proposal_phase: MemoirBookRevisionProposalPhase | null;
  requested_ai_model: string | null;
  resolved_ai_model: string | null;
  can_cancel: boolean;
  can_retry: boolean;
  error_message: string;
}

export interface MemoirBookAnswerApplicationJob extends MemoirBookRevisionJob {
  answer_count: number;
  unresolved_count: number;
  changed_chapter_count: number;
  summary: string;
  already_applied: boolean;
  can_apply: boolean;
}

export interface MemoirBookEditorialRevisionJob extends MemoirBookRevisionJob {
  selected_action_count: number;
}

export interface MemoirBookRevisionFeedbackJob extends MemoirBookRevisionJob {
  changed_chapter_count: number;
  summary: string;
}

export type MemoirBookRevisionDraftJob = MemoirBookRevisionFeedbackJob;

export type MemoirBookRevisionJobForSource<
  Source extends MemoirBookRevisionCommandSource,
> = Source extends "review_answers"
  ? MemoirBookAnswerApplicationJob
  : MemoirBookEditorialRevisionJob;

export type MemoirBookRevisionProposalStatus =
  | "ready"
  | "applied"
  | "dismissed"
  | "stale";

export type MemoirBookRevisionProposalPhase = "plan_ready" | "draft_ready";

export interface MemoirBookRevisionPlanItem {
  title: string;
  reason: string;
  approach: string;
  targets: string[];
}

export interface MemoirBookRevisionPlan {
  summary: string;
  items: MemoirBookRevisionPlanItem[];
  book_metadata_patch: Partial<Record<
    MemoirBookRevisionMetadataField,
    string
  >>;
}

export type MemoirBookRevisionMetadataField =
  | "title"
  | "subtitle"
  | "narrator_name"
  | "prepared_by"
  | "preface"
  | "afterword"
  | "back_cover_quote";

export interface MemoirBookRevisionMetadataChange {
  field: MemoirBookRevisionMetadataField;
  before_value: string;
  after_value: string;
}

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
  phase: MemoirBookRevisionProposalPhase;
  revision_plan: MemoirBookRevisionPlan | null;
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
    metadata_changes: MemoirBookRevisionMetadataChange[];
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

export interface MemoirBookCompletionSnapshot
  extends MemoirBookCompletionOverview {
  completion_job: MemoirBookCompletionJob;
  book_review: MemoirBookReview | null;
  book_review_job: MemoirBookReviewJob | null;
  book_issues: MemoirCompletionIssue[];
  book_output_available: boolean;
  answer_application_job: MemoirBookAnswerApplicationJob | null;
  editorial_revision_job: MemoirBookEditorialRevisionJob | null;
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
): Promise<MemoirBookCompletionSnapshot> {
  return memoirRequest<MemoirBookCompletionSnapshot>(
    bookCompletionPath(projectId, "/overview"),
  );
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
  source: MemoirBookRevisionCommandSource,
  suffix = "",
): string {
  const segment = source === "review_answers"
    ? "answer-application"
    : "editorial-revision";
  return `${bookReviewPath(projectId)}/${segment}/jobs${suffix}`;
}

export function enqueueMemoirBookRevisionJob<
  Source extends MemoirBookRevisionCommandSource,
>(
  projectId: string,
  source: Source,
  actionIds: string[] = [],
): Promise<MemoirBookRevisionJobForSource<Source>> {
  const payload = source === "review_answers"
    ? { ai_model: null }
    : { action_ids: actionIds, ai_model: null };
  return memoirAIRequest<MemoirBookRevisionJobForSource<Source>, typeof payload>(
    bookRevisionJobPath(projectId, source),
    payload,
    { timeoutProfile: "mutation" },
  );
}

export function cancelMemoirBookRevisionJob<
  Source extends MemoirBookRevisionCommandSource,
>(
  projectId: string,
  jobId: string,
  source: Source,
): Promise<MemoirBookRevisionJobForSource<Source>> {
  return memoirRequest<MemoirBookRevisionJobForSource<Source>>(
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

export function memoirBookReviewDictationUrl(projectId: string): string {
  const config = readBackendConfig();
  return config.wsBaseUrl
    ? `${cleanBaseUrl(config.wsBaseUrl)}${bookReviewPath(projectId)}/dictation`
    : "";
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
): Promise<MemoirBookRevisionFeedbackJob> {
  return memoirAIRequest<
    MemoirBookRevisionFeedbackJob,
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
): Promise<MemoirBookRevisionFeedbackJob> {
  return memoirRequest<MemoirBookRevisionFeedbackJob>(
    bookRevisionProposalPath(projectId, proposalId, "/feedback/jobs/current"),
  );
}

export function enqueueMemoirBookRevisionDraft(
  projectId: string,
  proposalId: string,
): Promise<MemoirBookRevisionDraftJob> {
  return memoirAIRequest<
    MemoirBookRevisionDraftJob,
    { ai_model: null }
  >(
    bookRevisionProposalPath(projectId, proposalId, "/draft/jobs"),
    { ai_model: null },
    { timeoutProfile: "mutation" },
  );
}

export function getMemoirBookRevisionDraftJob(
  projectId: string,
  proposalId: string,
): Promise<MemoirBookRevisionDraftJob> {
  return memoirRequest<MemoirBookRevisionDraftJob>(
    bookRevisionProposalPath(projectId, proposalId, "/draft/jobs/current"),
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
    { retry_job_id: string | null; ai_model: null }
  >(
    bookCompletionPath(projectId, "/jobs"),
    { retry_job_id: retryJobId, ai_model: null },
    { timeoutProfile: "mutation" },
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
