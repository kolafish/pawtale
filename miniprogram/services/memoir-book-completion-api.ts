import type { MemoirBookCompletionOverview } from "../domain/chapter-completion";
import type { MemoirBookCompletion } from "../domain/voice-chat";
import { memoirNarrativeProjectPath, memoirRequest } from "./memoir-api-client";


function bookCompletionPath(projectId: string, suffix = ""): string {
  return memoirNarrativeProjectPath(projectId, `/book-completion${suffix}`);
}

export function getMemoirBookCompletionOverview(
  projectId: string,
): Promise<MemoirBookCompletionOverview> {
  return memoirRequest<MemoirBookCompletionOverview>(bookCompletionPath(projectId, "/overview"));
}

export function prepareMemoirBookCompletionChapter(
  projectId: string,
  chapterId: string,
): Promise<MemoirBookCompletion> {
  return memoirRequest<MemoirBookCompletion>(
    bookCompletionPath(projectId, `/chapters/${encodeURIComponent(chapterId)}/prepare`),
    { method: "POST" },
  );
}
