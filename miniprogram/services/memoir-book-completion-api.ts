import type { MemoirBookCompletionOverview } from "../domain/chapter-completion";
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
  };
  error_message: string;
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
