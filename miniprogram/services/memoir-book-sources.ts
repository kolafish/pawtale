import type {
  MemoirBookChapterSourceRef,
} from "../domain/memoir-book";

export function sourceRefKey(
  ref: Pick<MemoirBookChapterSourceRef, "utterance_id" | "voice_session_id" | "message_id">,
): string {
  return ref.utterance_id || `${ref.voice_session_id}:${ref.message_id}`;
}
