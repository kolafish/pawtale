import type {
  MemoirBookDraftBlockAssembly,
  MemoirChapterDraftBlockAssembly,
  MemoirDraftBlock,
  MemoirDraftBlockListResponse,
  MemoirDraftBlockManualRevisionRequest,
  MemoirDraftBlockUnlockRequest,
} from "../domain/draft-blocks";
import {
  memoirNarrativeProjectPath,
  memoirRequest,
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

export function unlockMemoirDraftBlock(
  projectId: string,
  blockId: string,
  data: MemoirDraftBlockUnlockRequest,
): Promise<MemoirDraftBlock> {
  return memoirRequest<MemoirDraftBlock>(blockPath(projectId, blockId, "/unlock"), {
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
