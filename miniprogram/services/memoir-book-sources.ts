import type {
  MemoirBookChapterDraft,
  MemoirBookChapterSourceRef,
  MemoirBookPlanChapter,
} from "../domain/voice-chat";

export const PENDING_SOURCE_UTTERANCE_IDS_KEY = "pending_source_utterance_ids";
export const SOURCE_ASSIGNMENT_UPDATED_AT_KEY = "source_assignment_updated_at";
export const REMOVED_SOURCE_UTTERANCE_IDS_KEY = "removed_source_utterance_ids";
export const SOURCE_REFS_REMOVED_AT_KEY = "source_refs_removed_at";
export const DRAFT_STATUS_REASON_KEY = "status_reason";

export type MemoirBookDraftStatusReason = "new_sources_assigned" | "sources_deleted";
export type MemoirBookSourceChangeMetadataKey =
  | typeof PENDING_SOURCE_UTTERANCE_IDS_KEY
  | typeof SOURCE_ASSIGNMENT_UPDATED_AT_KEY
  | typeof REMOVED_SOURCE_UTTERANCE_IDS_KEY
  | typeof SOURCE_REFS_REMOVED_AT_KEY;

export type MemoirBookChapterMetadata = Record<string, unknown> & Partial<{
  [PENDING_SOURCE_UTTERANCE_IDS_KEY]: string[];
  [SOURCE_ASSIGNMENT_UPDATED_AT_KEY]: string;
  [REMOVED_SOURCE_UTTERANCE_IDS_KEY]: string[];
  [SOURCE_REFS_REMOVED_AT_KEY]: string;
}>;

export type MemoirBookDraftMetadata = Record<string, unknown> & Partial<{
  [DRAFT_STATUS_REASON_KEY]: MemoirBookDraftStatusReason;
  [REMOVED_SOURCE_UTTERANCE_IDS_KEY]: string[];
}>;

const SOURCE_CHANGE_METADATA_KEYS: MemoirBookSourceChangeMetadataKey[] = [
  PENDING_SOURCE_UTTERANCE_IDS_KEY,
  SOURCE_ASSIGNMENT_UPDATED_AT_KEY,
  REMOVED_SOURCE_UTTERANCE_IDS_KEY,
  SOURCE_REFS_REMOVED_AT_KEY,
];

export function sourceRefKey(
  ref: Pick<MemoirBookChapterSourceRef, "utterance_id" | "voice_session_id" | "message_id">,
): string {
  return ref.utterance_id || `${ref.voice_session_id}:${ref.message_id}`;
}

export function sourceRefMatchesUtterance(ref: MemoirBookChapterSourceRef, utteranceId: string): boolean {
  return Boolean(utteranceId) && ref.utterance_id === utteranceId;
}

export function chapterSourceRefs(chapter: Pick<MemoirBookPlanChapter, "source_refs">): MemoirBookChapterSourceRef[] {
  return [...(chapter.source_refs || [])];
}

export function uniqueSourceRefs(refs: MemoirBookChapterSourceRef[]): MemoirBookChapterSourceRef[] {
  const seen = new Set<string>();
  const result: MemoirBookChapterSourceRef[] = [];
  refs.forEach((ref) => {
    const key = sourceRefKey(ref);
    if (seen.has(key)) {
      return;
    }
    seen.add(key);
    result.push(ref);
  });
  return result;
}

export function sourceIdsFromMetadata(
  metadata: Record<string, unknown> | null | undefined,
  key: typeof PENDING_SOURCE_UTTERANCE_IDS_KEY | typeof REMOVED_SOURCE_UTTERANCE_IDS_KEY,
): string[] {
  const value = metadata?.[key];
  if (!Array.isArray(value)) {
    return [];
  }
  return value.filter((item): item is string => typeof item === "string" && Boolean(item.trim()));
}

export function pendingSourceUtteranceIds(metadata: Record<string, unknown> | null | undefined): string[] {
  return sourceIdsFromMetadata(metadata, PENDING_SOURCE_UTTERANCE_IDS_KEY);
}

export function removedSourceUtteranceIds(metadata: Record<string, unknown> | null | undefined): string[] {
  return sourceIdsFromMetadata(metadata, REMOVED_SOURCE_UTTERANCE_IDS_KEY);
}

function draftStatusReason(draft: MemoirBookChapterDraft | null): MemoirBookDraftStatusReason | "" {
  const reason = draft?.metadata_json?.[DRAFT_STATUS_REASON_KEY];
  return reason === "new_sources_assigned" || reason === "sources_deleted" ? reason : "";
}

export function sourceSupplementCount(
  metadata: Record<string, unknown> | null | undefined,
  draft: MemoirBookChapterDraft | null,
): number {
  const count = pendingSourceUtteranceIds(metadata).length;
  if (count) {
    return count;
  }
  return draftStatusReason(draft) === "new_sources_assigned" ? 1 : 0;
}

export function removedSourceCount(
  metadata: Record<string, unknown> | null | undefined,
  draft: MemoirBookChapterDraft | null,
): number {
  const count = removedSourceUtteranceIds(metadata).length;
  if (count) {
    return count;
  }
  return draftStatusReason(draft) === "sources_deleted" ? 1 : 0;
}

export function clearSourceChangeMetadata(
  metadata: Record<string, unknown> | null | undefined,
): MemoirBookChapterMetadata {
  const next = { ...(metadata || {}) } as MemoirBookChapterMetadata;
  SOURCE_CHANGE_METADATA_KEYS.forEach((key) => {
    delete next[key];
  });
  return next;
}
