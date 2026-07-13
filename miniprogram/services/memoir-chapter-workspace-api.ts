import type {
  MemoirChapterGapAuditJob,
  MemoirChapterGapAuditRequest,
  MemoirChapterNodeReorderRequest,
  MemoirChapterNodeStoryMoveRequest,
  MemoirChapterNodeUpdateRequest,
  MemoirChapterWorkspace,
  MemoirChapterWorkspaceContract,
  MemoirNarrativeDecision,
  MemoirNarrativeDecisionRequest,
  MemoirNarrativeGap,
  MemoirNarrativeGapAction,
  MemoirNarrativeGapResolutionRequest,
  MemoirStorySlotResolutionRequest,
  MemoirStorySlotResolutionStatus,
  MemoirStoryMergeRequest,
  MemoirVerificationDecision,
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

function narrativeTargetPath(
  projectId: string,
  target: "claims" | "stories" | "chapter-contracts",
  targetId: string,
  suffix: string,
): string {
  return memoirNarrativeProjectPath(
    projectId,
    `/${target}/${encodeURIComponent(targetId)}${suffix}`,
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

export function decideMemoirClaim(
  projectId: string,
  claimId: string,
  action: MemoirVerificationDecision,
  data: MemoirNarrativeDecisionRequest,
): Promise<MemoirNarrativeDecision> {
  return memoirRequest<MemoirNarrativeDecision>(
    narrativeTargetPath(projectId, "claims", claimId, `/${action}`),
    { method: "POST", data },
  );
}

export function decideMemoirStory(
  projectId: string,
  storyId: string,
  action: MemoirVerificationDecision,
  data: MemoirNarrativeDecisionRequest,
): Promise<MemoirNarrativeDecision> {
  return memoirRequest<MemoirNarrativeDecision>(
    narrativeTargetPath(projectId, "stories", storyId, `/${action}`),
    { method: "POST", data },
  );
}

export function excludeMemoirStoryFromChapter(
  projectId: string,
  contractId: string,
  storyId: string,
  data: MemoirNarrativeDecisionRequest,
): Promise<MemoirChapterWorkspaceContract> {
  return memoirRequest<MemoirChapterWorkspaceContract>(
    narrativeTargetPath(
      projectId,
      "chapter-contracts",
      contractId,
      `/stories/${encodeURIComponent(storyId)}/exclude`,
    ),
    { method: "POST", data },
  );
}

export function resolveMemoirStorySlot(
  projectId: string,
  storyId: string,
  slotKey: string,
  status: MemoirStorySlotResolutionStatus,
  data: MemoirStorySlotResolutionRequest,
): Promise<MemoirNarrativeDecision> {
  return memoirRequest<MemoirNarrativeDecision>(
    narrativeTargetPath(
      projectId,
      "stories",
      storyId,
      `/slots/${encodeURIComponent(slotKey)}/resolve`,
    ),
    {
      method: "POST",
      data: { ...data, status },
    },
  );
}

export function confirmMemoirChapterContract(
  projectId: string,
  contractId: string,
): Promise<MemoirChapterWorkspaceContract> {
  return memoirRequest<MemoirChapterWorkspaceContract>(
    narrativeTargetPath(projectId, "chapter-contracts", contractId, "/confirm"),
    { method: "POST", data: {} },
  );
}

export function updateMemoirChapterNode(
  projectId: string,
  contractId: string,
  nodeId: string,
  data: MemoirChapterNodeUpdateRequest,
): Promise<MemoirChapterWorkspaceContract> {
  return memoirRequest<MemoirChapterWorkspaceContract>(
    narrativeTargetPath(
      projectId,
      "chapter-contracts",
      contractId,
      `/nodes/${encodeURIComponent(nodeId)}`,
    ),
    { method: "PATCH", data },
  );
}

export function reorderMemoirChapterNodes(
  projectId: string,
  contractId: string,
  data: MemoirChapterNodeReorderRequest,
): Promise<MemoirChapterWorkspaceContract> {
  return memoirRequest<MemoirChapterWorkspaceContract>(
    narrativeTargetPath(projectId, "chapter-contracts", contractId, "/nodes/reorder"),
    { method: "POST", data },
  );
}

export function moveMemoirStoryBetweenChapterNodes(
  projectId: string,
  contractId: string,
  storyId: string,
  data: MemoirChapterNodeStoryMoveRequest,
): Promise<MemoirChapterWorkspaceContract> {
  return memoirRequest<MemoirChapterWorkspaceContract>(
    narrativeTargetPath(
      projectId,
      "chapter-contracts",
      contractId,
      `/stories/${encodeURIComponent(storyId)}/move`,
    ),
    { method: "POST", data },
  );
}

export function mergeMemoirStory(
  projectId: string,
  sourceStoryId: string,
  data: MemoirStoryMergeRequest,
): Promise<MemoirNarrativeDecision> {
  return memoirRequest<MemoirNarrativeDecision>(
    narrativeTargetPath(projectId, "stories", sourceStoryId, "/merge"),
    { method: "POST", data },
  );
}
