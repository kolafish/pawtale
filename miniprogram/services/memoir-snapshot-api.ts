import type {
  MemoirBookSnapshot,
  MemoirHomeSnapshot,
  MemoirDraftProgress,
  MemoirStorySnapshot,
} from "../domain/memoir-snapshots";
import { writeMemoirIdentitySnapshot } from "../stores/memoir-cache";
import { memoirRequest } from "./memoir-api-client";

function rememberSnapshotIdentity<T extends MemoirHomeSnapshot | MemoirBookSnapshot | MemoirStorySnapshot>(
  snapshot: T,
): T {
  writeMemoirIdentitySnapshot(snapshot.identity);
  return snapshot;
}

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

export async function getMemoirHomeSnapshot(): Promise<MemoirHomeSnapshot> {
  return rememberSnapshotIdentity(
    await memoirRequest<MemoirHomeSnapshot>("/api/mobile/memoir/home-snapshot"),
  );
}

export async function getMemoirBookSnapshot(): Promise<MemoirBookSnapshot> {
  return rememberSnapshotIdentity(
    await memoirRequest<MemoirBookSnapshot>("/api/mobile/memoir/book-snapshot"),
  );
}
