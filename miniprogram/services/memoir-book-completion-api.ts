import type { MemoirBookCompletionOverview } from "../domain/chapter-completion";
import type { MemoirChapterWorkspace } from "../domain/chapter-workspace";
import {
  memoirNarrativeProjectPath,
  memoirAIRequest,
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
  return memoirAIRequest<MemoirChapterWorkspace, { ai_model: null }>(
    memoirNarrativeProjectPath(
      projectId,
      `/book-completion/chapters/${encodeURIComponent(chapterId)}/prepare`,
    ),
    { ai_model: null },
  );
}

export function rewriteMemoirBookChapter(
  projectId: string,
  chapterId: string,
): Promise<MemoirChapterWorkspace> {
  return memoirAIRequest<MemoirChapterWorkspace, { ai_model: null }>(
    memoirNarrativeProjectPath(
      projectId,
      `/book-completion/chapters/${encodeURIComponent(chapterId)}/rewrite`,
    ),
    { ai_model: null },
  );
}
