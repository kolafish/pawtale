import type { MemoirChapterWorkspace } from "../domain/chapter-workspace";
import {
  memoirNarrativeProjectPath,
  memoirRequest,
} from "./memoir-api-client";


function chapterWorkspacePath(projectId: string, chapterId: string): string {
  return memoirNarrativeProjectPath(
    projectId,
    `/chapters/${encodeURIComponent(chapterId)}/workspace`,
  );
}

export function getMemoirChapterWorkspace(
  projectId: string,
  chapterId: string,
): Promise<MemoirChapterWorkspace> {
  return memoirRequest<MemoirChapterWorkspace>(chapterWorkspacePath(projectId, chapterId));
}

export function confirmMemoirChapterFact(
  projectId: string,
  confirmationId: string,
  statement: string,
): Promise<void> {
  return memoirRequest<void>(
    memoirNarrativeProjectPath(
      projectId,
      `/gaps/${encodeURIComponent(confirmationId)}/resolve`,
    ),
    {
      method: "POST",
      data: {
        idempotency_key: `chapter-confirmation:${confirmationId}`,
        note: statement,
      },
    },
  );
}
