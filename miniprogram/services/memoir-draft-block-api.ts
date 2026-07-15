import type {
  MemoirBookDraftBlockAssembly,
  MemoirChapterDraftBlockAssembly,
  MemoirDraftBlock,
  MemoirDraftBlockDecisionRequest,
  MemoirDraftBlockGenerationRequest,
  MemoirDraftBlockJob,
  MemoirDraftBlockListResponse,
  MemoirDraftBlockManualRevisionRequest,
  MemoirDraftBlockRevisionApplyRequest,
  MemoirDraftBlockRevisionJobRequest,
} from "../domain/draft-blocks";
import {
  memoirNarrativeProjectPath,
  memoirRequest,
  withSelectedAIModel,
} from "./memoir-api-client";


function chapterBlocksPath(projectId: string, chapterId: string, suffix = ""): string {
  return memoirNarrativeProjectPath(
    projectId,
    `/chapters/${encodeURIComponent(chapterId)}/draft-blocks${suffix}`,
  );
}

export function assembleMemoirBookDraftBlocks(
  projectId: string,
): Promise<MemoirBookDraftBlockAssembly> {
  return memoirRequest<MemoirBookDraftBlockAssembly>(
    memoirNarrativeProjectPath(projectId, "/book-output/assemble-from-draft-blocks"),
    {
      method: "POST",
      data: {},
    },
  );
}

function blockPath(projectId: string, blockId: string, suffix = ""): string {
  return memoirNarrativeProjectPath(
    projectId,
    `/draft-blocks/${encodeURIComponent(blockId)}${suffix}`,
  );
}

export function listMemoirDraftBlocks(
  projectId: string,
  chapterId: string,
): Promise<MemoirDraftBlockListResponse> {
  return memoirRequest<MemoirDraftBlockListResponse>(chapterBlocksPath(projectId, chapterId));
}

export function enqueueMemoirDraftBlockGeneration(
  projectId: string,
  chapterId: string,
  data: MemoirDraftBlockGenerationRequest = {},
): Promise<MemoirDraftBlockJob> {
  return memoirRequest<MemoirDraftBlockJob>(chapterBlocksPath(projectId, chapterId, "/generation-jobs"), {
    method: "POST",
    data: withSelectedAIModel({
      node_ids: data.node_ids || [],
      ai_model: data.ai_model,
    }),
  });
}

export function getMemoirDraftBlockJob(
  projectId: string,
  jobId: string,
): Promise<MemoirDraftBlockJob> {
  return memoirRequest<MemoirDraftBlockJob>(
    memoirNarrativeProjectPath(
      projectId,
      `/draft-block-generation-jobs/${encodeURIComponent(jobId)}`,
    ),
  );
}

export function manuallyReviseMemoirDraftBlock(
  projectId: string,
  blockId: string,
  data: MemoirDraftBlockManualRevisionRequest,
): Promise<MemoirDraftBlock> {
  return memoirRequest<MemoirDraftBlock>(blockPath(projectId, blockId, "/manual-revisions"), {
    method: "POST",
    data,
  });
}

export function decideMemoirDraftBlock(
  projectId: string,
  blockId: string,
  action: "confirm" | "lock" | "unlock",
  data: MemoirDraftBlockDecisionRequest,
): Promise<MemoirDraftBlock> {
  return memoirRequest<MemoirDraftBlock>(blockPath(projectId, blockId, `/${action}`), {
    method: "POST",
    data,
  });
}

export function enqueueMemoirDraftBlockRevision(
  projectId: string,
  blockId: string,
  data: MemoirDraftBlockRevisionJobRequest,
): Promise<MemoirDraftBlockJob> {
  return memoirRequest<MemoirDraftBlockJob>(blockPath(projectId, blockId, "/revision-jobs"), {
    method: "POST",
    data: withSelectedAIModel(data),
  });
}

export function applyMemoirDraftBlockRevision(
  projectId: string,
  blockId: string,
  data: MemoirDraftBlockRevisionApplyRequest,
): Promise<MemoirDraftBlock> {
  return memoirRequest<MemoirDraftBlock>(blockPath(projectId, blockId, "/apply-revision"), {
    method: "POST",
    data,
  });
}

export function assembleMemoirChapterDraftBlocks(
  projectId: string,
  chapterId: string,
): Promise<MemoirChapterDraftBlockAssembly> {
  return memoirRequest<MemoirChapterDraftBlockAssembly>(chapterBlocksPath(projectId, chapterId, "/assemble"), {
    method: "POST",
    data: {},
  });
}
