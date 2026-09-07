import type { MemoirChapterWorkspace } from "../domain/chapter-workspace";
import type { MemoirBookRevisionProposal } from "./memoir-book-completion-api";
import {
  memoirNarrativeProjectPath,
  memoirAIRequest,
  memoirRequest,
} from "./memoir-api-client";

function chapterWorkspacePath(projectId: string, chapterId: string): string {
  return memoirNarrativeProjectPath(
    projectId,
    `/chapters/${encodeURIComponent(chapterId)}/workspace`,
  );
}

export interface MemoirChapterSectionRegenerationJob {
  job_id: string | null;
  project_id: string;
  chapter_id: string;
  status: "idle" | "pending" | "running" | "succeeded" | "failed" | "cancelled";
  stage: string;
  material_count: number;
  processed_material_count: number;
  total_char_count: number;
  processed_char_count: number;
  batch_count: number;
  completed_batch_count: number;
  assigned_count: number;
  unassigned_count: number;
  section_count: number;
  requested_ai_model: string | null;
  resolved_ai_model: string | null;
  can_cancel: boolean;
  error_message: string;
}

export interface MemoirSectionMaterialPlacementRequest {
  target_section_id?: string;
  target_chapter_id?: string;
  new_chapter_title?: string;
  new_section_title?: string;
}

export interface MemoirSectionMaterialPlacementResult {
  project_id: string;
  utterance_id: string;
  chapter_id: string;
  section_id: string;
  created_chapter: boolean;
  created_section: boolean;
}

export function getMemoirChapterWorkspace(
  projectId: string,
  chapterId: string,
): Promise<MemoirChapterWorkspace> {
  return memoirRequest<MemoirChapterWorkspace>(chapterWorkspacePath(projectId, chapterId));
}

export function regenerateMemoirChapterSections(
  projectId: string,
  chapterId: string,
  expectedSections: Array<{ id: string; version: number }>,
): Promise<MemoirChapterSectionRegenerationJob> {
  return memoirAIRequest<
    MemoirChapterSectionRegenerationJob,
    {
      expected_sections: Array<{ id: string; version: number }>;
      ai_model: null;
    }
  >(
    memoirNarrativeProjectPath(
      projectId,
      `/chapters/${encodeURIComponent(chapterId)}/section-regeneration-jobs`,
    ),
    {
      expected_sections: expectedSections,
      ai_model: null,
    },
    { timeoutProfile: "mutation" },
  );
}

export function mergeMemoirChapterSections(
  projectId: string,
  targetSectionId: string,
  sourceSectionId: string,
): Promise<unknown> {
  return memoirRequest<unknown>(
    memoirNarrativeProjectPath(
      projectId,
      `/sections/${encodeURIComponent(targetSectionId)}/merge`,
    ),
    {
      method: "POST",
      data: {
        source_section_ids: [sourceSectionId],
      },
    },
  );
}

export function renameMemoirChapterSection(
  projectId: string,
  sectionId: string,
  title: string,
  expectedVersion: number,
): Promise<unknown> {
  return memoirRequest<unknown>(
    memoirNarrativeProjectPath(
      projectId,
      `/sections/${encodeURIComponent(sectionId)}`,
    ),
    {
      method: "PATCH",
      data: {
        title,
        expected_version: expectedVersion,
      },
    },
  );
}

export function getMemoirChapterSectionRegenerationJob(
  projectId: string,
  chapterId: string,
): Promise<MemoirChapterSectionRegenerationJob> {
  return memoirRequest<MemoirChapterSectionRegenerationJob>(
    memoirNarrativeProjectPath(
      projectId,
      `/chapters/${encodeURIComponent(chapterId)}/section-regeneration-jobs/current`,
    ),
  );
}

export function cancelMemoirChapterSectionRegenerationJob(
  projectId: string,
  chapterId: string,
  jobId: string,
): Promise<MemoirChapterSectionRegenerationJob> {
  return memoirRequest<MemoirChapterSectionRegenerationJob>(
    memoirNarrativeProjectPath(
      projectId,
      `/chapters/${encodeURIComponent(chapterId)}/section-regeneration-jobs/${encodeURIComponent(jobId)}`,
    ),
    { method: "DELETE" },
  );
}

export type MemoirChapterSectionRevisionOperation = "plan" | "feedback" | "draft";

export interface MemoirChapterSectionRevisionJob {
  job_id: string | null;
  project_id: string;
  operation: MemoirChapterSectionRevisionOperation;
  status: "idle" | "pending" | "running" | "succeeded" | "failed" | "cancelled";
  stage: string;
  proposal_id: string | null;
  requested_ai_model: string | null;
  resolved_ai_model: string | null;
  error_message: string;
}

function sectionRevisionPath(
  projectId: string,
  chapterId: string,
  sectionId: string,
): string {
  return memoirNarrativeProjectPath(
    projectId,
    `/chapters/${encodeURIComponent(chapterId)}/draft-sections/${encodeURIComponent(sectionId)}/revision`,
  );
}

export function enqueueMemoirChapterSectionRevision(
  projectId: string,
  chapterId: string,
  sectionId: string,
  operation: MemoirChapterSectionRevisionOperation,
  proposalId: string | null,
  feedback: string | null,
): Promise<MemoirChapterSectionRevisionJob> {
  return memoirAIRequest<
    MemoirChapterSectionRevisionJob,
    {
      operation: MemoirChapterSectionRevisionOperation;
      proposal_id: string | null;
      feedback: string | null;
      ai_model: null;
    }
  >(
    `${sectionRevisionPath(projectId, chapterId, sectionId)}/jobs`,
    {
      operation,
      proposal_id: proposalId,
      feedback: feedback?.trim() || null,
      ai_model: null,
    },
    { timeoutProfile: "mutation" },
  );
}

export function getMemoirChapterSectionRevisionJob(
  projectId: string,
  chapterId: string,
  sectionId: string,
): Promise<MemoirChapterSectionRevisionJob> {
  return memoirRequest<MemoirChapterSectionRevisionJob>(
    `${sectionRevisionPath(projectId, chapterId, sectionId)}/jobs/current`,
  );
}

export function getMemoirChapterSectionRevisionProposal(
  projectId: string,
  chapterId: string,
  sectionId: string,
  proposalId: string,
): Promise<MemoirBookRevisionProposal> {
  return memoirRequest<MemoirBookRevisionProposal>(
    `${sectionRevisionPath(projectId, chapterId, sectionId)}/proposals/${encodeURIComponent(proposalId)}`,
  );
}

function finalizeMemoirChapterSectionRevision(
  projectId: string,
  chapterId: string,
  sectionId: string,
  proposalId: string,
  action: "apply" | "dismiss",
): Promise<MemoirBookRevisionProposal> {
  return memoirRequest<MemoirBookRevisionProposal>(
    `${sectionRevisionPath(projectId, chapterId, sectionId)}/proposals/${encodeURIComponent(proposalId)}/${action}`,
    { method: "POST" },
  );
}

export const applyMemoirChapterSectionRevision = (
  projectId: string,
  chapterId: string,
  sectionId: string,
  proposalId: string,
) => finalizeMemoirChapterSectionRevision(
  projectId, chapterId, sectionId, proposalId, "apply",
);

export const dismissMemoirChapterSectionRevision = (
  projectId: string,
  chapterId: string,
  sectionId: string,
  proposalId: string,
) => finalizeMemoirChapterSectionRevision(
  projectId, chapterId, sectionId, proposalId, "dismiss",
);

export function placeMemoirChapterMaterial(
  projectId: string,
  utteranceId: string,
  placement: MemoirSectionMaterialPlacementRequest,
): Promise<MemoirSectionMaterialPlacementResult> {
  return memoirRequest<MemoirSectionMaterialPlacementResult>(
    memoirNarrativeProjectPath(
      projectId,
      `/section-materials/${encodeURIComponent(utteranceId)}/place`,
    ),
    {
      method: "POST",
      data: placement,
    },
  );
}
