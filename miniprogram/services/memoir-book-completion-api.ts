import type { MemoirBookCompletion } from "../domain/voice-chat";
import { memoirNarrativeProjectPath, memoirRequest } from "./memoir-api-client";


function bookCompletionPath(projectId: string, suffix = ""): string {
  return memoirNarrativeProjectPath(projectId, `/book-completion${suffix}`);
}

export function getMemoirBookCompletion(projectId: string): Promise<MemoirBookCompletion> {
  return memoirRequest<MemoirBookCompletion>(bookCompletionPath(projectId));
}

export function prepareMemoirBookCompletion(projectId: string): Promise<MemoirBookCompletion> {
  return memoirRequest<MemoirBookCompletion>(bookCompletionPath(projectId, "/prepare"), {
    method: "POST",
  });
}
