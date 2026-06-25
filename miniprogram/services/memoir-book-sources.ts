import type {
  MemoirBookChapterSourceRef,
  MemoirBookPlanChapter,
} from "../domain/voice-chat";

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
