import type {
  MemoirBookPlan,
  MemoirBookPlanChapter,
  MemoirBookSourceAssignmentRequest,
  MemoirBookSourceMoveRequest,
  MemoirVoiceTranscriptItem,
} from "../domain/voice-chat";
import {
  chapterSourceRefs,
  sourceRefMatchesUtterance,
} from "./memoir-book-sources";

export interface ChapterPickerOption {
  label: string;
  value: string;
}

export interface TranscriptSourceMoveRequestResult {
  request: MemoirBookSourceMoveRequest | null;
  error: string;
}

export function sortedBookChapters(plan: MemoirBookPlan | null): MemoirBookPlanChapter[] {
  return [...(plan?.chapters || [])].sort((left, right) => left.sort_order - right.sort_order);
}

export function bookChapterPickerOption(
  chapter: Pick<MemoirBookPlanChapter, "id" | "title">,
  index: number,
): ChapterPickerOption {
  return {
    label: `第 ${index + 1} 章 ${chapter.title || "未命名章节"}`,
    value: chapter.id,
  };
}

export function bookChapterPickerOptions(
  chapters: Pick<MemoirBookPlanChapter, "id" | "title">[],
): ChapterPickerOption[] {
  return chapters.map(bookChapterPickerOption);
}

export function planChapterPickerOptions(plan: MemoirBookPlan | null): ChapterPickerOption[] {
  return bookChapterPickerOptions(sortedBookChapters(plan));
}

export function sourceAssignmentRequest(chapterId: string, utteranceId: string): MemoirBookSourceAssignmentRequest {
  return {
    assignments: [
      {
        chapter_id: chapterId,
        utterance_ids: [utteranceId],
      },
    ],
  };
}

export function transcriptSourceChapters(
  item: MemoirVoiceTranscriptItem,
  plan: MemoirBookPlan | null,
): MemoirBookPlanChapter[] {
  return sortedBookChapters(plan).filter((chapter) => (
    chapterSourceRefs(chapter).some((ref) => sourceRefMatchesUtterance(ref, item.id))
  ));
}

export function transcriptChapterAssignOptions(
  item: MemoirVoiceTranscriptItem,
  plan: MemoirBookPlan | null,
): ChapterPickerOption[] {
  if (item.is_included_in_book || item.included_chapters?.length) {
    return [];
  }
  return planChapterPickerOptions(plan);
}

export function transcriptChapterMoveOptions(
  item: MemoirVoiceTranscriptItem,
  plan: MemoirBookPlan | null,
): ChapterPickerOption[] {
  const sourceChapters = transcriptSourceChapters(item, plan);
  if (sourceChapters.length !== 1) {
    return [];
  }
  const sourceChapter = sourceChapters[0];
  if (chapterSourceRefs(sourceChapter).length <= 1) {
    return [];
  }
  return sortedBookChapters(plan).flatMap((chapter, index) => (
    chapter.id === sourceChapter.id ? [] : [bookChapterPickerOption(chapter, index)]
  ));
}

export function transcriptSourceMoveRequest(
  item: MemoirVoiceTranscriptItem,
  plan: MemoirBookPlan | null,
  targetChapterId: string,
): TranscriptSourceMoveRequestResult {
  const targetChapter = sortedBookChapters(plan).find((chapter) => chapter.id === targetChapterId);
  const sourceChapters = transcriptSourceChapters(item, plan);
  const sourceChapter = sourceChapters.length === 1 ? sourceChapters[0] : null;
  if (!plan || !sourceChapter) {
    return { request: null, error: "没有找到可移动的本章素材" };
  }
  if (chapterSourceRefs(sourceChapter).length <= 1) {
    return { request: null, error: "本章只剩 1 条素材；请在书稿页合并或删除章节来保留素材" };
  }
  if (!targetChapter || targetChapter.id === sourceChapter.id) {
    return { request: null, error: "请选择要移动到的章节" };
  }
  return {
    request: {
      source_chapter_id: sourceChapter.id,
      target_chapter_id: targetChapterId,
      utterance_id: item.id,
    },
    error: "",
  };
}
