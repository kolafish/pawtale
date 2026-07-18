import type { MemoirBookCompletionOverview } from "../domain/chapter-completion";
import type { MemoirChapterWorkspace } from "../domain/chapter-workspace";
import {
  memoirNarrativeProjectPath,
  memoirRequest,
} from "./memoir-api-client";


function bookCompletionPath(projectId: string, suffix = ""): string {
  return memoirNarrativeProjectPath(projectId, `/book-completion${suffix}`);
}

export function getMemoirBookCompletionOverview(
  projectId: string,
): Promise<MemoirBookCompletionOverview> {
  return memoirRequest<MemoirBookCompletionOverview>(bookCompletionPath(projectId, "/overview"));
}

export function prepareMemoirBookChapter(
  projectId: string,
  chapterId: string,
): Promise<MemoirChapterWorkspace> {
  return memoirRequest<MemoirChapterWorkspace>(
    memoirNarrativeProjectPath(
      projectId,
      `/book-completion/chapters/${encodeURIComponent(chapterId)}/prepare`,
    ),
    {
      method: "POST",
      data: {},
    },
  );
}

export function rewriteMemoirBookChapter(
  projectId: string,
  chapterId: string,
): Promise<MemoirChapterWorkspace> {
  return memoirRequest<MemoirChapterWorkspace>(
    memoirNarrativeProjectPath(
      projectId,
      `/book-completion/chapters/${encodeURIComponent(chapterId)}/rewrite`,
    ),
    {
      method: "POST",
      data: {},
    },
  );
}
