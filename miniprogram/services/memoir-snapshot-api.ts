import type {
  MemoirBookSnapshot,
  MemoirDraftProgress,
  MemoirStorySnapshot,
} from "../domain/memoir-snapshots";
import { memoirRequest } from "./memoir-api-client";

export async function getMemoirStorySnapshot(): Promise<MemoirStorySnapshot> {
  return memoirRequest<MemoirStorySnapshot>("/api/mobile/memoir/story-snapshot");
}

export function getMemoirDraftProgress(projectId: string, voiceSessionId = ""): Promise<MemoirDraftProgress> {
  const query = voiceSessionId ? `?voice_session_id=${encodeURIComponent(voiceSessionId)}` : "";
  return memoirRequest<MemoirDraftProgress>(`/api/mobile/memoir/projects/${projectId}/draft-progress${query}`);
}

export function retryMemoirDraftProgress(projectId: string): Promise<MemoirDraftProgress> {
  return memoirRequest<MemoirDraftProgress>(`/api/mobile/memoir/projects/${projectId}/draft-progress/retry`, { method: "POST" });
}

export function getMemoirBookSnapshot(): Promise<MemoirBookSnapshot> {
  return memoirRequest<MemoirBookSnapshot>("/api/mobile/memoir/book-snapshot");
}
