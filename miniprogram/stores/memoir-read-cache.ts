import type { MemoirIdentity } from "../domain/memoir";
import type {
  MemoirBookSnapshot,
  MemoirHomeSnapshot,
} from "../domain/memoir-snapshots";
import type { MemoirChapterWorkspace } from "../domain/chapter-workspace";
import {
  isMemoirIdentity,
  readMemoirIdentitySnapshot,
} from "./memoir-cache";
import {
  clearMemoirCacheForUser,
  markMemoirCacheResourcesStaleForUser,
  readMemoirCache,
  writeMemoirCache,
  type MemoirCacheDescriptor,
  type MemoirCacheReadResult,
  type MemoirCacheResource,
} from "./memoir-cache-store";
import { readMemoirStore } from "./session";

const HOME_CACHE_SCHEMA_VERSION = 1;
const BOOK_CACHE_SCHEMA_VERSION = 3;
const CHAPTER_WORKSPACE_CACHE_SCHEMA_VERSION = 1;
const HOME_CACHE_MAX_AGE_MS = 30 * 60 * 1000;
const BOOK_CACHE_MAX_AGE_MS = 15 * 60 * 1000;
const CHAPTER_WORKSPACE_CACHE_MAX_AGE_MS = 10 * 60 * 1000;
const PAGE_CACHE_RESOURCES: ReadonlySet<MemoirCacheResource> = new Set([
  "home",
  "book",
  "chapter_workspace",
]);

function isObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === "object");
}

function hasServerRevision(value: Record<string, unknown>): boolean {
  return Number.isInteger(value.server_revision)
    && Number(value.server_revision) >= 0;
}

function isHomeSnapshot(value: unknown): value is MemoirHomeSnapshot {
  return isObject(value)
    && hasServerRevision(value)
    && isMemoirIdentity(value.identity)
    && (
      value.latest_transcript === undefined
      || value.latest_transcript === null
      || (isObject(value.latest_transcript) && typeof value.latest_transcript.id === "string")
    );
}

function isBookSnapshot(value: unknown): value is MemoirBookSnapshot {
  return isObject(value)
    && hasServerRevision(value)
    && isMemoirIdentity(value.identity)
    && isObject(value.drafts_by_chapter_id)
    && isObject(value.automation_events)
    && Array.isArray(value.automation_events.items)
    && isObject(value.image_assets)
    && Array.isArray(value.image_assets.items)
    && Array.isArray(value.share_video_counts);
}

function isChapterWorkspace(value: unknown): value is MemoirChapterWorkspace {
  return isObject(value)
    && hasServerRevision(value)
    && typeof value.project_id === "string"
    && typeof value.chapter_id === "string"
    && isObject(value.completion_summary)
    && Array.isArray(value.sections)
    && Array.isArray(value.draft_sections);
}

function descriptor<T>(
  identity: MemoirIdentity,
  resource: "home" | "book" | "chapter_workspace",
  options: {
    projectId?: string;
    resourceId?: string;
    cacheSchemaVersion: number;
    maxAgeMs: number;
    isPayload(value: unknown): value is T;
  },
): MemoirCacheDescriptor<T> {
  return {
    scope: {
      userId: identity.id,
      storageOwnerId: identity.storage_owner_id,
      projectId: options.projectId ?? identity.active_project_id,
      resource,
      resourceId: options.resourceId,
    },
    cacheSchemaVersion: options.cacheSchemaVersion,
    maxAgeMs: options.maxAgeMs,
    isPayload: options.isPayload,
  };
}

function cachedIdentity(): MemoirIdentity | null {
  try {
    return readMemoirIdentitySnapshot().identity || null;
  } catch (error) {
    console.warn("read memoir snapshot identity failed", error);
    return null;
  }
}

export function readCachedMemoirIdentity(): MemoirIdentity | null {
  return cachedIdentity();
}

function homeDescriptor(identity: MemoirIdentity): MemoirCacheDescriptor<MemoirHomeSnapshot> {
  return descriptor(identity, "home", {
    cacheSchemaVersion: HOME_CACHE_SCHEMA_VERSION,
    maxAgeMs: HOME_CACHE_MAX_AGE_MS,
    isPayload: isHomeSnapshot,
  });
}

function bookDescriptor(identity: MemoirIdentity): MemoirCacheDescriptor<MemoirBookSnapshot> {
  return descriptor(identity, "book", {
    cacheSchemaVersion: BOOK_CACHE_SCHEMA_VERSION,
    maxAgeMs: BOOK_CACHE_MAX_AGE_MS,
    isPayload: isBookSnapshot,
  });
}

function chapterDescriptor(
  identity: MemoirIdentity,
  projectId: string,
  chapterId: string,
): MemoirCacheDescriptor<MemoirChapterWorkspace> {
  return descriptor(identity, "chapter_workspace", {
    projectId,
    resourceId: chapterId,
    cacheSchemaVersion: CHAPTER_WORKSPACE_CACHE_SCHEMA_VERSION,
    maxAgeMs: CHAPTER_WORKSPACE_CACHE_MAX_AGE_MS,
    isPayload: isChapterWorkspace,
  });
}

export function readCachedMemoirHome(): MemoirCacheReadResult<MemoirHomeSnapshot> {
  const identity = cachedIdentity();
  return identity
    ? readMemoirCache(homeDescriptor(identity))
    : { status: "miss" };
}

export function writeCachedMemoirHome(snapshot: MemoirHomeSnapshot): void {
  writeMemoirCache(
    homeDescriptor(snapshot.identity),
    snapshot,
    { serverRevision: snapshot.server_revision },
  );
}

export function readCachedMemoirBook(): MemoirCacheReadResult<MemoirBookSnapshot> {
  const identity = cachedIdentity();
  return identity
    ? readMemoirCache(bookDescriptor(identity))
    : { status: "miss" };
}

export function writeCachedMemoirBook(snapshot: MemoirBookSnapshot): void {
  writeMemoirCache(
    bookDescriptor(snapshot.identity),
    snapshot,
    { serverRevision: snapshot.server_revision },
  );
}

export function readCachedMemoirChapterWorkspace(
  projectId: string,
  chapterId: string,
): MemoirCacheReadResult<MemoirChapterWorkspace> {
  const identity = cachedIdentity();
  return identity
    ? readMemoirCache(chapterDescriptor(identity, projectId, chapterId))
    : { status: "miss" };
}

export function writeCachedMemoirChapterWorkspace(
  workspace: MemoirChapterWorkspace,
): void {
  const identity = cachedIdentity();
  if (!identity) {
    return;
  }
  writeMemoirCache(
    chapterDescriptor(identity, workspace.project_id, workspace.chapter_id),
    workspace,
    { serverRevision: workspace.server_revision },
  );
}

export function markCurrentUserMemoirSnapshotsStale(): void {
  try {
    const userId = readMemoirStore().cache_identity?.user_id || "";
    markMemoirCacheResourcesStaleForUser(userId, PAGE_CACHE_RESOURCES);
  } catch (error) {
    console.warn("mark current memoir snapshots stale failed", error);
  }
}

export function clearCurrentUserMemoirSnapshots(): void {
  try {
    const userId = readMemoirStore().cache_identity?.user_id || "";
    clearMemoirCacheForUser(userId);
  } catch (error) {
    console.warn("clear current memoir snapshots failed", error);
  }
}
