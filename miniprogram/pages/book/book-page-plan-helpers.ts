import type {
  MemoirBookPlanStructureCommandRequest,
} from "../../domain/memoir-book";
import { pageUrl } from "../../services/page-input";
import type { BookChapterView } from "./book-view-model";

export const CHAPTER_MOVE_DIRECTIONS = ["up", "down"] as const;
export type ChapterMoveDirection = typeof CHAPTER_MOVE_DIRECTIONS[number];

export function bookChapterDetailUrl(projectId: string, chapterId: string): string {
  return pageUrl("/pages/chapter-workspace/index", { projectId, chapterId });
}

export function bookExportPageUrl(projectId: string, action = "files"): string {
  return pageUrl("/pages/book-export/index", { projectId, action });
}

export function findBookChapter(chapters: BookChapterView[], chapterId: string): BookChapterView | null {
  return chapters.find((chapter) => chapter.id === chapterId) || null;
}

export function chapterMoveCommand(
  chapters: BookChapterView[],
  chapterId: string,
  direction: ChapterMoveDirection,
): MemoirBookPlanStructureCommandRequest | null {
  const fromIndex = chapters.findIndex((chapter) => chapter.id === chapterId);
  const toIndex = direction === "up" ? fromIndex - 1 : fromIndex + 1;
  if (fromIndex < 0 || toIndex < 0 || toIndex >= chapters.length) {
    return null;
  }
  return {
    type: "move_chapter",
    chapter_id: chapterId,
    direction,
  };
}
