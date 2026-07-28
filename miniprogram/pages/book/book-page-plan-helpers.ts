import type {
  MemoirBookPlanStructureCommandRequest,
} from "../../domain/memoir-book";
import { pageUrl } from "../../services/page-input";
import type { BookChapterView, BookData } from "./book-view-model";
import { chapterCollectionData, trimText } from "./book-view-model";

export const CHAPTER_MOVE_DIRECTIONS = ["up", "down"] as const;
export type ChapterMoveDirection = typeof CHAPTER_MOVE_DIRECTIONS[number];

export function bookChapterDetailUrl(projectId: string, chapterId: string): string {
  return pageUrl("/pages/chapter-workspace/index", { projectId, chapterId });
}

export function bookExportPageUrl(projectId: string, action = "preview"): string {
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

export function removeLocalChapterPatch(
  data: Pick<BookData, "chapters" | "splitSourceSelections">,
  chapterId: string,
): Partial<BookData> | null {
  const chapters = data.chapters.filter((chapter) => chapter.id !== chapterId);
  if (chapters.length === data.chapters.length) {
    return null;
  }
  return {
    ...chapterCollectionData(chapters, data.splitSourceSelections),
    error: "",
  };
}

export function toggleBookSplitSourcePatch(
  data: Pick<BookData, "chapters" | "splitSourceSelections">,
  chapterId: string,
  utteranceId: string,
): Partial<BookData> | null {
  const chapter = findBookChapter(data.chapters, chapterId);
  if (!chapter || !utteranceId || chapter.source_refs.length <= 1 || chapter.draftBusy) {
    return null;
  }
  const selected = new Set(data.splitSourceSelections[chapterId] || []);
  if (selected.has(utteranceId)) {
    selected.delete(utteranceId);
  } else {
    selected.add(utteranceId);
  }
  const splitSourceSelections = { ...data.splitSourceSelections };
  if (selected.size) {
    splitSourceSelections[chapterId] = Array.from(selected);
  } else {
    delete splitSourceSelections[chapterId];
  }
  return {
    splitSourceSelections,
    ...chapterCollectionData(data.chapters, splitSourceSelections),
    error: "",
  };
}

export interface SplitSelectedChapterSourcesPlan {
  command?: MemoirBookPlanStructureCommandRequest;
  error?: string;
  patch?: Partial<BookData>;
}

export function splitSelectedChapterSourcesPlan(
  data: Pick<BookData, "chapters" | "splitSourceSelections">,
  chapterId: string,
): SplitSelectedChapterSourcesPlan | null {
  const chapter = findBookChapter(data.chapters, chapterId);
  const selectedIds = new Set(data.splitSourceSelections[chapterId] || []);
  if (!chapter || !selectedIds.size || chapter.draftBusy) {
    return null;
  }
  if (selectedIds.size >= chapter.source_refs.length) {
    return { error: "拆分时至少保留 1 条原始讲述在原章节" };
  }
  const title = trimText(chapter.editingTitle || chapter.title) || "未命名章节";
  const splitSourceSelections = { ...data.splitSourceSelections };
  delete splitSourceSelections[chapterId];
  return {
    patch: { splitSourceSelections },
    command: {
      type: "split_chapter",
      chapter_id: chapterId,
      title: `${title}（拆出）`,
      utterance_ids: Array.from(selectedIds),
    },
  };
}

export function toggleBookChapterSourcesPatch(
  data: Pick<BookData, "chapters" | "splitSourceSelections">,
  chapterId: string,
): Partial<BookData> {
  const chapters = data.chapters.map((chapter) => (
    chapter.id === chapterId
      ? { ...chapter, sourceListExpanded: !chapter.sourceListExpanded }
      : chapter
  ));
  return chapterCollectionData(chapters, data.splitSourceSelections);
}
