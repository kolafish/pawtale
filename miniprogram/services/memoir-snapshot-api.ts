import type {
  MemoirBookSnapshot,
  MemoirDemoStory,
  MemoirDraftProgress,
  MemoirStorySnapshot,
} from "../domain/memoir-snapshots";
import { writeMemoirIdentitySnapshot } from "../stores/memoir-cache";
import { memoirRequest } from "./memoir-api-client";
import { request } from "./request";

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

export async function getMemoirBookSnapshot(): Promise<MemoirBookSnapshot> {
  const snapshot = await memoirRequest<MemoirBookSnapshot>("/api/mobile/memoir/book-snapshot");
  writeMemoirIdentitySnapshot(snapshot.identity);
  return snapshot;
}

export function getMemoirDemoStory(): Promise<MemoirDemoStory> {
  return request<MemoirDemoStory>("/api/mobile/memoir/demo-story");
}
