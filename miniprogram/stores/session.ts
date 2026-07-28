import type { MemoirIdentity, Project } from "../domain/memoir";
import type {
  MemoirVoiceSessionDetail,
  VoiceChatTranscriptLine,
} from "../domain/memoir-voice-session";

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
    return {
      ...store,
      cache_write_failed_at: new Date().toISOString(),
    };
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

export function setMemoirCacheIdentity(identity: MemoirCacheIdentity): MemoirSessionStore {
  const current = readMemoirStore();
  const currentIdentity = current.cache_identity;
  if (
    !currentIdentity
    || currentIdentity.user_id !== identity.user_id
    || (currentIdentity.storage_owner_id || "") !== (identity.storage_owner_id || "")
  ) {
    return replaceMemoirStore({ cache_identity: identity });
  }
  const currentProjectId = currentIdentity.project_id || "";
  const nextProjectId = identity.project_id ?? currentIdentity.project_id;
  if (identity.project_id !== undefined && currentProjectId !== identity.project_id) {
    return replaceMemoirStore({ cache_identity: identity });
  }
  const nextIdentity: MemoirCacheIdentity = {
    user_id: identity.user_id,
    storage_owner_id: identity.storage_owner_id,
    project_id: nextProjectId,
  };
  if (
    currentIdentity.user_id === nextIdentity.user_id
    && currentIdentity.storage_owner_id === nextIdentity.storage_owner_id
    && currentIdentity.project_id === nextIdentity.project_id
  ) {
    return current;
  }
  return writeMemoirStore({ cache_identity: nextIdentity });
}
