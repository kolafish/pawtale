import type {
  MemoirBookChapterSourceRef,
} from "../domain/memoir-book";

export function sourceRefKey(
  ref: Pick<MemoirBookChapterSourceRef, "utterance_id" | "voice_session_id" | "message_id">,
): string {
  return ref.utterance_id || `${ref.voice_session_id}:${ref.message_id}`;
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
