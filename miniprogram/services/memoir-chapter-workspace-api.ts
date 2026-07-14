import type {
  MemoirChapterGapAuditJob,
  MemoirChapterGapAuditRequest,
  MemoirChapterContractRevisionRequest,
  MemoirChapterWorkspace,
  MemoirChapterWorkspaceContract,
  MemoirChapterStoryDecisionRequest,
  MemoirNarrativeGap,
  MemoirNarrativeGapAction,
  MemoirNarrativeGapResolutionRequest,
} from "../domain/chapter-workspace";
import {
  memoirNarrativeProjectPath,
  memoirRequest,
  withSelectedAIModel,
} from "./memoir-api-client";


function chapterWorkspacePath(projectId: string, chapterId: string, suffix = ""): string {
  return memoirNarrativeProjectPath(
    projectId,
    `/chapters/${encodeURIComponent(chapterId)}/workspace${suffix}`,
  );
}

function gapPath(projectId: string, gapId: string, action: MemoirNarrativeGapAction): string {
  return memoirNarrativeProjectPath(
    projectId,
    `/gaps/${encodeURIComponent(gapId)}/${action}`,
  );
}

function chapterContractPath(projectId: string, contractId: string, suffix = ""): string {
  return memoirNarrativeProjectPath(
    projectId,
    `/chapter-contracts/${encodeURIComponent(contractId)}${suffix}`,
  );
}

export function getMemoirChapterWorkspace(
  projectId: string,
  chapterId: string,
): Promise<MemoirChapterWorkspace> {
  return memoirRequest<MemoirChapterWorkspace>(chapterWorkspacePath(projectId, chapterId));
}

export function enqueueMemoirChapterWorkspaceAudit(
  projectId: string,
  chapterId: string,
  data: MemoirChapterGapAuditRequest = {},
): Promise<MemoirChapterGapAuditJob> {
  return memoirRequest<MemoirChapterGapAuditJob>(
    chapterWorkspacePath(projectId, chapterId, "/audits"),
    {
      method: "POST",
      data: withSelectedAIModel({
        include_semantic_audit: data.include_semantic_audit ?? true,
        ai_model: data.ai_model,
      }),
    },
  );
}

export function createMemoirChapterContractRevision(
  projectId: string,
  chapterId: string,
  data: MemoirChapterContractRevisionRequest,
): Promise<MemoirChapterWorkspaceContract> {
  return memoirRequest<MemoirChapterWorkspaceContract>(
    memoirNarrativeProjectPath(
      projectId,
      `/chapters/${encodeURIComponent(chapterId)}/contract/revisions`,
    ),
    { method: "POST", data },
  );
}

export function includeMemoirStoryInChapter(
  projectId: string,
  contractId: string,
  storyId: string,
  data: MemoirChapterStoryDecisionRequest,
): Promise<MemoirChapterWorkspaceContract> {
  return memoirRequest<MemoirChapterWorkspaceContract>(
    chapterContractPath(
      projectId,
      contractId,
      `/stories/${encodeURIComponent(storyId)}/include`,
    ),
    { method: "POST", data },
  );
}

export function resolveMemoirNarrativeGap(
  projectId: string,
  gapId: string,
  action: MemoirNarrativeGapAction,
  data: MemoirNarrativeGapResolutionRequest,
): Promise<MemoirNarrativeGap> {
  return memoirRequest<MemoirNarrativeGap>(gapPath(projectId, gapId, action), {
    method: "POST",
    data,
  });
}
