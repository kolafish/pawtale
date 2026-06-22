import type { MemoirIdentity, Project, User } from "../domain/memoir";
import type {
  MemoirVoiceSessionDetail,
  VoiceChatTranscriptLine,
} from "../domain/voice-chat";

const STORE_KEY = "pawtale_memoir_session";

export interface MemoirSessionStore {
  cache_identity?: MemoirCacheIdentity;
  identity_cache?: MemoirCacheMetadata;
  voice_session_cache?: MemoirVoiceSessionCache;
  identity?: MemoirIdentity;
  project?: Project | null;
  cache_write_failed_at?: string;
}

export interface MemoirCacheIdentity {
  user_id: string;
  storage_owner_id?: string;
  project_id?: string;
}

export interface MemoirCacheMetadata {
  version: number;
  cached_at: string;
  stale?: boolean;
}

export interface MemoirCachedVoiceSession {
  detail?: MemoirVoiceSessionDetail;
  transcript_lines: VoiceChatTranscriptLine[];
  pending_close?: MemoirPendingVoiceClose;
  cached_at: string;
  stale?: boolean;
}

export interface MemoirPendingVoiceClose {
  reason: "hide" | "unload" | "connection_error";
  captured_at: string;
  transcript_lines: VoiceChatTranscriptLine[];
  live_text: string;
  recording: boolean;
}

export interface MemoirVoiceSessionCache {
  version: number;
  sessions: Record<string, MemoirCachedVoiceSession>;
}

export function readMemoirStore(): MemoirSessionStore {
  return wx.getStorageSync<MemoirSessionStore>(STORE_KEY) || {};
}

function persistMemoirStore(store: MemoirSessionStore): MemoirSessionStore {
  try {
    wx.setStorageSync(STORE_KEY, store);
    return store;
  } catch {
    if (!store.voice_session_cache) {
      return {
        ...store,
        cache_write_failed_at: new Date().toISOString(),
      };
    }
    const fallback = { ...store };
    delete fallback.voice_session_cache;
    fallback.cache_write_failed_at = new Date().toISOString();
    try {
      wx.setStorageSync(STORE_KEY, fallback);
      return fallback;
    } catch {
      return fallback;
    }
  }
}

export function writeMemoirStore(patch: MemoirSessionStore): MemoirSessionStore {
  const next = {
    ...readMemoirStore(),
    ...patch,
  };
  delete next.cache_write_failed_at;
  return persistMemoirStore(next);
}

export function replaceMemoirStore(next: MemoirSessionStore): MemoirSessionStore {
  delete next.cache_write_failed_at;
  return persistMemoirStore(next);
}

export function cacheIdentityFromUser(
  user: Pick<User, "id" | "storage_owner_id">,
  project?: Pick<Project, "id">,
): MemoirCacheIdentity {
  return {
    user_id: user.id,
    storage_owner_id: user.storage_owner_id,
    project_id: project?.id,
  };
}

function sameCacheIdentity(current: MemoirCacheIdentity | undefined, next: MemoirCacheIdentity): boolean {
  if (!current) {
    return true;
  }
  if (current.user_id !== next.user_id) {
    return false;
  }
  if ((current.storage_owner_id || "") !== (next.storage_owner_id || "")) {
    return false;
  }
  const currentProjectId = current.project_id || "";
  const nextProjectId = next.project_id || "";
  if ((currentProjectId || nextProjectId) && currentProjectId !== nextProjectId) {
    return false;
  }
  return true;
}

export function ensureMemoirStoreIdentity(identity: MemoirCacheIdentity): MemoirSessionStore {
  const current = readMemoirStore();
  const hasCachedMemoirData = Boolean(
    current.project
    || current.identity
    || current.identity_cache
    || current.voice_session_cache,
  );
  if (!current.cache_identity && hasCachedMemoirData) {
    return replaceMemoirStore({ cache_identity: identity });
  }
  if (!sameCacheIdentity(current.cache_identity, identity)) {
    return replaceMemoirStore({ cache_identity: identity });
  }
  const nextIdentity = {
    user_id: identity.user_id,
    storage_owner_id: identity.storage_owner_id,
    project_id: identity.project_id ?? current.cache_identity?.project_id,
  };
  if (
    current.cache_identity?.user_id === nextIdentity.user_id
    && current.cache_identity.storage_owner_id === nextIdentity.storage_owner_id
    && current.cache_identity.project_id === nextIdentity.project_id
  ) {
    return current;
  }
  return writeMemoirStore({ cache_identity: nextIdentity });
}
