import type {
  MemoirChapterGapAuditJob,
  MemoirChapterGapAuditRequest,
  MemoirChapterWorkspace,
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
