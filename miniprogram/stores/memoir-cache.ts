import type { MemoirIdentity } from "../domain/memoir";
import type {
  MemoirChatMessage,
  MemoirVoiceSessionDetail,
  MemoirVoiceUtterance,
  VoiceChatTranscriptLine,
} from "../domain/voice-chat";
import {
  readMemoirStore,
  writeMemoirStore,
  type MemoirCachedVoiceSession,
  type MemoirCacheMetadata,
  type MemoirVoiceSessionCache,
} from "./session";

const MEMOIR_CACHE_VERSION = 3;
const IDENTITY_CACHE_MAX_AGE_MS = 30 * 60 * 1000;
const VOICE_SESSION_CACHE_MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000;
const VOICE_SESSION_CACHE_MAX_ITEMS = 12;

export interface MemoirIdentitySnapshot {
  identity?: MemoirIdentity;
  hasIdentity: boolean;
  cachedAt?: string;
  isFresh: boolean;
}

export interface MemoirVoiceSessionSnapshot {
  detail?: MemoirVoiceSessionDetail;
  transcriptLines: VoiceChatTranscriptLine[];
  hasSession: boolean;
  cachedAt?: string;
  isFresh: boolean;
}

function freshCacheMeta(): MemoirCacheMetadata {
  return {
    version: MEMOIR_CACHE_VERSION,
    cached_at: new Date().toISOString(),
    stale: false,
  };
}

function validCacheMeta(meta?: MemoirCacheMetadata): meta is MemoirCacheMetadata {
  return Boolean(meta && meta.version === MEMOIR_CACHE_VERSION && meta.cached_at);
}

function validVoiceSessionCache(cache?: MemoirVoiceSessionCache): cache is MemoirVoiceSessionCache {
  return Boolean(cache && cache.version === MEMOIR_CACHE_VERSION && cache.sessions);
}

function validCachedVoiceSession(entry?: MemoirCachedVoiceSession): entry is MemoirCachedVoiceSession {
  return Boolean(entry && Array.isArray(entry.transcript_lines) && entry.cached_at);
}

function cacheAgeMs(cachedAt?: string): number {
  if (!cachedAt) {
    return Number.POSITIVE_INFINITY;
  }
  const timestamp = new Date(cachedAt).getTime();
  if (Number.isNaN(timestamp)) {
    return Number.POSITIVE_INFINITY;
  }
  return Date.now() - timestamp;
}

function isMetaFresh(meta: MemoirCacheMetadata | undefined, maxAgeMs: number): boolean {
  return Boolean(validCacheMeta(meta) && !meta.stale && cacheAgeMs(meta.cached_at) <= maxAgeMs);
}

function cacheMatchesIdentity(): boolean {
  const store = readMemoirStore();
  if (!store.cache_identity || !store.identity) {
    return false;
  }
  if (store.identity.id !== store.cache_identity.user_id) {
    return false;
  }
  const cachedProjectId = store.cache_identity.project_id || "";
  const identityProjectId = store.identity.active_project_id || store.identity.active_project?.id || "";
  return !cachedProjectId || !identityProjectId || cachedProjectId === identityProjectId;
}

function compactVoiceSessionEntries(
  sessions: Record<string, MemoirCachedVoiceSession>,
): Record<string, MemoirCachedVoiceSession> {
  return Object.fromEntries(
    Object.entries(sessions)
      .filter(([, entry]) => cacheAgeMs(entry.cached_at) <= VOICE_SESSION_CACHE_MAX_AGE_MS)
      .sort(([, left], [, right]) => String(right.cached_at).localeCompare(String(left.cached_at)))
      .slice(0, VOICE_SESSION_CACHE_MAX_ITEMS),
  );
}

function voiceSessionCacheWith(sessions: Record<string, MemoirCachedVoiceSession>): MemoirVoiceSessionCache {
  return {
    version: MEMOIR_CACHE_VERSION,
    sessions: compactVoiceSessionEntries(sessions),
  };
}

function timestampMs(value?: string | null): number {
  if (!value) {
    return 0;
  }
  const timestamp = new Date(value).getTime();
  return Number.isNaN(timestamp) ? 0 : timestamp;
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

function preferDetailTranscriptLines(
  detailLines: VoiceChatTranscriptLine[],
  existingLines: VoiceChatTranscriptLine[],
): VoiceChatTranscriptLine[] {
  return detailLines.length >= existingLines.length ? detailLines : existingLines;
}

export function transcriptLinesFromVoiceSessionDetail(
  detail: MemoirVoiceSessionDetail,
): VoiceChatTranscriptLine[] {
  const utteranceByMessageId = new Map(
    detail.utterances
      .filter((utterance) => utterance.chat_message_id)
      .map((utterance) => [utterance.chat_message_id as string, utterance]),
  );
  const userMessages = detail.messages
    .filter((message) => message.role === "user" && message.content_text.trim())
    .sort(compareVoiceMessages);
  if (userMessages.length) {
    return userMessages.map((message) => {
      const utterance = utteranceByMessageId.get(message.id);
      return {
        id: message.id,
        text: message.content_text,
        message_id: message.id,
        utterance_id: utterance?.id,
        audio_file_id: message.audio_file_id ?? utterance?.audio_file_id,
        transcript_version_id: message.transcript_version_id,
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
      message_id: utterance.chat_message_id || undefined,
      utterance_id: utterance.id,
      audio_file_id: utterance.audio_file_id,
      start_ms: utterance.start_ms,
      end_ms: utterance.end_ms,
    }));
}

export function readMemoirIdentitySnapshot(): MemoirIdentitySnapshot {
  const store = readMemoirStore();
  const hasIdentity = cacheMatchesIdentity() && validCacheMeta(store.identity_cache);
  return {
    identity: hasIdentity ? store.identity : undefined,
    hasIdentity,
    cachedAt: hasIdentity ? store.identity_cache?.cached_at : undefined,
    isFresh: hasIdentity ? isMetaFresh(store.identity_cache, IDENTITY_CACHE_MAX_AGE_MS) : false,
  };
}

export function writeMemoirIdentitySnapshot(identity: MemoirIdentity): void {
  writeMemoirStore({
    identity,
    identity_cache: freshCacheMeta(),
    project: identity.active_project,
  });
}

export function readMemoirVoiceSessionSnapshot(voiceSessionId: string): MemoirVoiceSessionSnapshot {
  const emptySnapshot = {
    transcriptLines: [],
    hasSession: false,
    isFresh: false,
  };
  if (!voiceSessionId) {
    return emptySnapshot;
  }
  const cache = readMemoirStore().voice_session_cache;
  if (!validVoiceSessionCache(cache)) {
    return emptySnapshot;
  }
  const entry = cache.sessions[voiceSessionId];
  if (!validCachedVoiceSession(entry)) {
    return emptySnapshot;
  }
  return {
    detail: entry.detail,
    transcriptLines: entry.transcript_lines,
    hasSession: true,
    cachedAt: entry.cached_at,
    isFresh: !entry.stale && cacheAgeMs(entry.cached_at) <= VOICE_SESSION_CACHE_MAX_AGE_MS,
  };
}

export function writeMemoirVoiceSessionSnapshot(
  voiceSessionId: string,
  snapshot: {
    detail?: MemoirVoiceSessionDetail;
    transcriptLines?: VoiceChatTranscriptLine[];
  },
): void {
  if (!voiceSessionId) {
    return;
  }
  const store = readMemoirStore();
  const cache = validVoiceSessionCache(store.voice_session_cache)
    ? store.voice_session_cache
    : { version: MEMOIR_CACHE_VERSION, sessions: {} };
  const existing = cache.sessions[voiceSessionId];
  const existingLines = validCachedVoiceSession(existing) ? existing.transcript_lines : [];
  const detailLines = snapshot.detail ? transcriptLinesFromVoiceSessionDetail(snapshot.detail) : [];
  const transcriptLines = snapshot.transcriptLines !== undefined
    ? snapshot.transcriptLines
    : preferDetailTranscriptLines(detailLines, existingLines);
  const detail = snapshot.detail || (validCachedVoiceSession(existing) ? existing.detail : undefined);
  if (!detail && !transcriptLines.length) {
    return;
  }
  writeMemoirStore({
    voice_session_cache: voiceSessionCacheWith({
      ...cache.sessions,
      [voiceSessionId]: {
        detail,
        transcript_lines: transcriptLines,
        cached_at: new Date().toISOString(),
        stale: false,
      },
    }),
  });
}

export function markMemoirVoiceSessionSnapshotStale(voiceSessionId: string): void {
  if (!voiceSessionId) {
    return;
  }
  const store = readMemoirStore();
  const cache = validVoiceSessionCache(store.voice_session_cache)
    ? store.voice_session_cache
    : undefined;
  const existing = cache?.sessions[voiceSessionId];
  if (!cache || !validCachedVoiceSession(existing)) {
    return;
  }
  writeMemoirStore({
    voice_session_cache: voiceSessionCacheWith({
      ...cache.sessions,
      [voiceSessionId]: {
        ...existing,
        stale: true,
      },
    }),
  });
}
