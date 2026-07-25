import type {
  MemoirBookSnapshot,
  MemoirHomeSnapshot,
} from "../domain/memoir-snapshots";
import { writeMemoirIdentitySnapshot } from "../stores/memoir-cache";
import { cacheIdentityFromUser, ensureMemoirStoreIdentity } from "../stores/session";
import { memoirRequest } from "./memoir-api-client";

function rememberSnapshotIdentity<T extends MemoirHomeSnapshot | MemoirBookSnapshot>(
  snapshot: T,
): T {
  const identity = snapshot.identity;
  ensureMemoirStoreIdentity(
    cacheIdentityFromUser(identity, identity.active_project || undefined),
  );
  writeMemoirIdentitySnapshot(identity);
  return snapshot;
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
