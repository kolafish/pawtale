import type { MemoirBookCompletionOverview } from "../domain/chapter-completion";
import type { MemoirChapterPreparationJob } from "../domain/chapter-workspace";
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
): Promise<MemoirChapterPreparationJob> {
  return memoirRequest<MemoirChapterPreparationJob>(
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
