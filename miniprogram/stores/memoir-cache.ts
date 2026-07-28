import type { MemoirIdentity } from "../domain/memoir";
import type {
  MemoirChatMessage,
  MemoirVoiceSessionDetail,
  MemoirVoiceUtterance,
  VoiceChatTranscriptLine,
} from "../domain/memoir-voice-session";
import {
  listMemoirCacheScopes,
  readMemoirCache,
  removeMemoirCache,
  writeMemoirCache,
  type MemoirCacheDescriptor,
  type MemoirCacheReadResult,
  type MemoirCacheWriteStatus,
} from "./memoir-cache-store";
import {
  readMemoirStore,
  replaceMemoirStore,
  setMemoirCacheIdentity,
  type MemoirCachedVoiceSession,
  type MemoirCacheIdentity,
  type MemoirPendingVoiceClose,
  type MemoirVoiceSessionCache,
} from "./session";
import { timestampMs } from "../services/time";

const LEGACY_CACHE_VERSION = 3;
const IDENTITY_CACHE_SCHEMA_VERSION = 1;
const VOICE_SESSION_CACHE_SCHEMA_VERSION = 1;
const IDENTITY_CACHE_MAX_AGE_MS = 30 * 60 * 1000;
const VOICE_SESSION_CACHE_MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000;
const VOICE_SESSION_CACHE_MAX_ITEMS = 12;

interface MemoirVoiceSessionCachePayload {
  detail?: MemoirVoiceSessionDetail;
  transcript_lines: VoiceChatTranscriptLine[];
  pending_close?: MemoirPendingVoiceClose;
}

export interface MemoirIdentitySnapshot {
  identity?: MemoirIdentity;
  hasIdentity: boolean;
  cachedAt?: string;
  isFresh: boolean;
}

export interface MemoirVoiceSessionSnapshot {
  detail?: MemoirVoiceSessionDetail;
  transcriptLines: VoiceChatTranscriptLine[];
  pendingClose?: MemoirPendingVoiceClose;
  hasSession: boolean;
  cachedAt?: string;
  isFresh: boolean;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === "object");
}

export function isMemoirIdentity(value: unknown): value is MemoirIdentity {
  return isObject(value)
    && typeof value.id === "string"
    && typeof value.email === "string"
    && typeof value.active_project_id === "string"
    && Array.isArray(value.projects);
}

function isVoiceSessionPayload(value: unknown): value is MemoirVoiceSessionCachePayload {
  return isObject(value) && Array.isArray(value.transcript_lines);
}

function currentCacheIdentity(): MemoirCacheIdentity | null {
  const identity = readMemoirStore().cache_identity;
  return identity?.user_id ? identity : null;
}

function identityDescriptor(
  identity: MemoirCacheIdentity,
): MemoirCacheDescriptor<MemoirIdentity> {
  return {
    scope: {
      userId: identity.user_id,
      storageOwnerId: identity.storage_owner_id,
      projectId: identity.project_id,
      resource: "identity",
    },
    cacheSchemaVersion: IDENTITY_CACHE_SCHEMA_VERSION,
    maxAgeMs: IDENTITY_CACHE_MAX_AGE_MS,
    isPayload: isMemoirIdentity,
  };
}

function voiceSessionDescriptor(
  identity: MemoirCacheIdentity,
  voiceSessionId: string,
): MemoirCacheDescriptor<MemoirVoiceSessionCachePayload> {
  return {
    scope: {
      userId: identity.user_id,
      storageOwnerId: identity.storage_owner_id,
      projectId: identity.project_id,
      resource: "voice_session",
      resourceId: voiceSessionId,
    },
    cacheSchemaVersion: VOICE_SESSION_CACHE_SCHEMA_VERSION,
    maxAgeMs: VOICE_SESSION_CACHE_MAX_AGE_MS,
    isPayload: isVoiceSessionPayload,
  };
}

function validLegacyTimestamp(value?: string): boolean {
  return Boolean(value && !Number.isNaN(new Date(value).getTime()));
}

function validLegacyVoiceSessionCache(
  cache?: MemoirVoiceSessionCache,
): cache is MemoirVoiceSessionCache {
  return Boolean(
    cache
    && cache.version === LEGACY_CACHE_VERSION
    && cache.sessions
    && typeof cache.sessions === "object",
  );
}

function validLegacyVoiceSession(
  entry?: MemoirCachedVoiceSession,
): entry is MemoirCachedVoiceSession {
  return Boolean(
    entry
    && Array.isArray(entry.transcript_lines)
    && validLegacyTimestamp(entry.cached_at),
  );
}

function legacyReadResult<T>(
  payload: T,
  cachedAt: string,
  stale: boolean | undefined,
  maxAgeMs: number,
): MemoirCacheReadResult<T> {
  const ageMs = Math.max(0, Date.now() - new Date(cachedAt).getTime());
  return {
    status: stale || ageMs > maxAgeMs ? "stale" : "fresh",
    payload,
    cachedAt,
    localInvalidationRevision: 0,
  };
}

function legacyIdentityMatchesScope(
  identity: MemoirIdentity,
  scope: MemoirCacheIdentity,
): boolean {
  const projectId = identity.active_project_id || identity.active_project?.id || "";
  return identity.id === scope.user_id
    && (identity.storage_owner_id || "") === (scope.storage_owner_id || "")
    && (!scope.project_id || !projectId || scope.project_id === projectId);
}

function migrationWriteSucceeded(status: MemoirCacheWriteStatus): boolean {
  return status === "written" || status === "ignored_older";
}

function removeLegacyIdentity(): boolean {
  const next = { ...readMemoirStore() };
  delete next.identity;
  delete next.identity_cache;
  delete next.project;
  return !replaceMemoirStore(next).cache_write_failed_at;
}

function removeLegacyVoiceSession(voiceSessionId: string): boolean {
  const store = readMemoirStore();
  if (!validLegacyVoiceSessionCache(store.voice_session_cache)) {
    return true;
  }
  const sessions = { ...store.voice_session_cache.sessions };
  delete sessions[voiceSessionId];
  const next = { ...store };
  if (Object.keys(sessions).length) {
    next.voice_session_cache = {
      version: LEGACY_CACHE_VERSION,
      sessions,
    };
  } else {
    delete next.voice_session_cache;
  }
  return !replaceMemoirStore(next).cache_write_failed_at;
}

function migrateLegacyMemoirCache(): boolean {
  const store = readMemoirStore();
  const identity = store.cache_identity;
  if (!identity) {
    return true;
  }
  const hasLegacyData = Boolean(
    store.identity
    || store.identity_cache
    || store.project
    || store.voice_session_cache,
  );
  if (!hasLegacyData) {
    return true;
  }
  let migrated = true;
  const legacyMeta = store.identity_cache;
  if (
    isMemoirIdentity(store.identity)
    && legacyIdentityMatchesScope(store.identity, identity)
    && legacyMeta?.version === LEGACY_CACHE_VERSION
    && validLegacyTimestamp(legacyMeta.cached_at)
  ) {
    const written = migrationWriteSucceeded(
      writeMemoirCache(identityDescriptor(identity), store.identity, {
        cachedAtMs: new Date(legacyMeta.cached_at).getTime(),
        stale: legacyMeta.stale,
      }),
    );
    migrated = written && removeLegacyIdentity();
  }
  if (validLegacyVoiceSessionCache(store.voice_session_cache)) {
    Object.entries(store.voice_session_cache.sessions)
      .filter((entry): entry is [string, MemoirCachedVoiceSession] => (
        validLegacyVoiceSession(entry[1])
      ))
      .forEach(([voiceSessionId, legacy]) => {
        const written = migrationWriteSucceeded(
          writeMemoirCache(
            voiceSessionDescriptor(identity, voiceSessionId),
            {
              detail: legacy.detail,
              transcript_lines: legacy.transcript_lines,
              pending_close: legacy.pending_close,
            },
            {
              cachedAtMs: new Date(legacy.cached_at).getTime(),
              stale: legacy.stale,
            },
          ),
        );
        migrated = written && removeLegacyVoiceSession(voiceSessionId) && migrated;
      });
  }
  if (migrated) {
    return !replaceMemoirStore({ cache_identity: identity }).cache_write_failed_at;
  }
  return false;
}

function readVoiceSessionCache(
  voiceSessionId: string,
): {
  descriptor?: MemoirCacheDescriptor<MemoirVoiceSessionCachePayload>;
  cached: MemoirCacheReadResult<MemoirVoiceSessionCachePayload>;
} {
  const identity = currentCacheIdentity();
  if (!identity || !voiceSessionId) {
    return { cached: { status: "miss" } };
  }
  migrateLegacyMemoirCache();
  const descriptor = voiceSessionDescriptor(identity, voiceSessionId);
  const cached = readMemoirCache(descriptor);
  if (cached.status !== "miss") {
    return { descriptor, cached };
  }
  const legacyCache = readMemoirStore().voice_session_cache;
  const legacy = validLegacyVoiceSessionCache(legacyCache)
    ? legacyCache.sessions[voiceSessionId]
    : undefined;
  return {
    descriptor,
    cached: validLegacyVoiceSession(legacy)
      ? legacyReadResult(
        {
          detail: legacy.detail,
          transcript_lines: legacy.transcript_lines,
          pending_close: legacy.pending_close,
        },
        legacy.cached_at,
        legacy.stale,
        VOICE_SESSION_CACHE_MAX_AGE_MS,
      )
      : cached,
  };
}

function pruneVoiceSessionCache(identity: MemoirCacheIdentity): void {
  listMemoirCacheScopes(identity.user_id, "voice_session")
    .sort((left, right) => right.cachedAt.localeCompare(left.cachedAt))
    .slice(VOICE_SESSION_CACHE_MAX_ITEMS)
    .forEach(({ scope }) => removeMemoirCache(scope));
}

function writeVoiceSessionCache(
  descriptor: MemoirCacheDescriptor<MemoirVoiceSessionCachePayload>,
  payload: MemoirVoiceSessionCachePayload,
  stale = false,
): void {
  const status = writeMemoirCache(descriptor, payload, { stale });
  const identity = currentCacheIdentity();
  if (status === "written" && identity) {
    pruneVoiceSessionCache(identity);
  }
}

function compareVoiceMessages(left: MemoirChatMessage, right: MemoirChatMessage): number {
  return left.turn_index - right.turn_index
    || timestampMs(left.created_at) - timestampMs(right.created_at)
    || left.id.localeCompare(right.id);
}

function compareVoiceUtterances(left: MemoirVoiceUtterance, right: MemoirVoiceUtterance): number {
  return timestampMs(left.created_at) - timestampMs(right.created_at)
    || left.id.localeCompare(right.id);
}

export function transcriptLinesFromVoiceSessionDetail(
  detail: MemoirVoiceSessionDetail,
): VoiceChatTranscriptLine[] {
  const utteranceByMessageId = new Map(
    detail.utterances
      .filter((utterance) => utterance.chat_message_id)
      .map((utterance) => [utterance.chat_message_id as string, utterance]),
  );
  const textMessages = detail.messages
    .filter((message) => (
      message.role === "user"
      || message.role === "assistant"
      || message.role === "interviewer"
    ) && message.content_text.trim())
    .sort(compareVoiceMessages);
  if (textMessages.length) {
    return textMessages.map((message) => {
      const utterance = utteranceByMessageId.get(message.id);
      return {
        id: message.id,
        text: message.content_text,
        role: message.role,
        message_id: message.id,
        utterance_id: utterance?.id,
        audio_file_id: message.audio_file_id ?? utterance?.audio_file_id,
        transcript_version_id: message.transcript_version_id,
        metadata_json: message.metadata_json || {},
        start_ms: utterance?.start_ms,
        end_ms: utterance?.end_ms,
      };
    });
  }
  return [...detail.utterances]
    .filter((utterance) => (utterance.transcript_edited || utterance.transcript_raw).trim())
    .sort(compareVoiceUtterances)
    .map((utterance) => ({
      id: utterance.chat_message_id || utterance.id,
      text: utterance.transcript_edited || utterance.transcript_raw,
      role: "user",
      message_id: utterance.chat_message_id || undefined,
      utterance_id: utterance.id,
      audio_file_id: utterance.audio_file_id,
      start_ms: utterance.start_ms,
      end_ms: utterance.end_ms,
    }));
}

export function readMemoirIdentitySnapshot(): MemoirIdentitySnapshot {
  const identity = currentCacheIdentity();
  if (identity) {
    migrateLegacyMemoirCache();
  }
  let cached = identity
    ? readMemoirCache(identityDescriptor(identity))
    : { status: "miss" as const };
  const store = cached.status === "miss" ? readMemoirStore() : null;
  if (
    identity
    && store
    && isMemoirIdentity(store.identity)
    && legacyIdentityMatchesScope(store.identity, identity)
    && store.identity_cache?.version === LEGACY_CACHE_VERSION
    && validLegacyTimestamp(store.identity_cache.cached_at)
  ) {
    cached = legacyReadResult(
      store.identity,
      store.identity_cache.cached_at,
      store.identity_cache.stale,
      IDENTITY_CACHE_MAX_AGE_MS,
    );
  }
  return {
    identity: cached.payload,
    hasIdentity: Boolean(cached.payload),
    cachedAt: cached.cachedAt,
    isFresh: cached.status === "fresh",
  };
}

export function writeMemoirIdentitySnapshot(identity: MemoirIdentity): void {
  const legacyMigrated = migrateLegacyMemoirCache();
  const cacheIdentity = {
    user_id: identity.id,
    storage_owner_id: identity.storage_owner_id,
    project_id: identity.active_project_id,
  };
  const currentIdentity = currentCacheIdentity();
  const changesScope = Boolean(
    currentIdentity
    && (
      currentIdentity.user_id !== cacheIdentity.user_id
      || (currentIdentity.storage_owner_id || "") !== (cacheIdentity.storage_owner_id || "")
      || (currentIdentity.project_id || "") !== cacheIdentity.project_id
    ),
  );
  if (!legacyMigrated && changesScope) {
    console.warn("preserve legacy memoir cache until scoped migration succeeds");
    return;
  }
  setMemoirCacheIdentity(cacheIdentity);
  writeMemoirCache(identityDescriptor(cacheIdentity), identity);
}

export function readMemoirVoiceSessionSnapshot(
  voiceSessionId: string,
): MemoirVoiceSessionSnapshot {
  const cached = readVoiceSessionCache(voiceSessionId).cached;
  return {
    detail: cached.payload?.detail,
    transcriptLines: cached.payload?.transcript_lines || [],
    pendingClose: cached.payload?.pending_close,
    hasSession: Boolean(cached.payload),
    cachedAt: cached.cachedAt,
    isFresh: cached.status === "fresh",
  };
}

export function writeMemoirVoiceSessionSnapshot(
  voiceSessionId: string,
  snapshot: {
    detail?: MemoirVoiceSessionDetail;
    transcriptLines?: VoiceChatTranscriptLine[];
  },
): void {
  const { descriptor, cached } = readVoiceSessionCache(voiceSessionId);
  if (!descriptor) {
    return;
  }
  const existing = cached.payload;
  const detailLines = snapshot.detail
    ? transcriptLinesFromVoiceSessionDetail(snapshot.detail)
    : [];
  const transcriptLines = snapshot.transcriptLines !== undefined
    ? snapshot.transcriptLines
    : (
      detailLines.length >= (existing?.transcript_lines.length || 0)
        ? detailLines
        : existing?.transcript_lines || []
    );
  const detail = snapshot.detail || existing?.detail;
  if (!detail && !transcriptLines.length) {
    return;
  }
  writeVoiceSessionCache(descriptor, {
    detail,
    transcript_lines: transcriptLines,
    pending_close: existing?.pending_close,
  });
}

export function writeMemoirVoiceSessionPendingClose(
  voiceSessionId: string,
  pendingClose: MemoirPendingVoiceClose,
): void {
  const { descriptor, cached } = readVoiceSessionCache(voiceSessionId);
  if (!descriptor) {
    return;
  }
  writeVoiceSessionCache(
    descriptor,
    {
      ...cached.payload,
      transcript_lines: pendingClose.transcript_lines,
      pending_close: pendingClose,
    },
    true,
  );
}

export function clearMemoirVoiceSessionPendingClose(voiceSessionId: string): void {
  const { descriptor, cached } = readVoiceSessionCache(voiceSessionId);
  if (!descriptor || !cached.payload?.pending_close) {
    return;
  }
  const payload = { ...cached.payload };
  delete payload.pending_close;
  writeVoiceSessionCache(descriptor, payload);
}
