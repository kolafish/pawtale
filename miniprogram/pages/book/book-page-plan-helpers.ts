import type {
  MemoirBookPlanStructureCommandRequest,
} from "../../domain/voice-chat";
import { pageUrl } from "../../services/page-input";
import type { BookChapterView, BookData } from "./book-view-model";
import { chapterCollectionData } from "./book-view-model";

export const CHAPTER_MOVE_DIRECTIONS = ["up", "down"] as const;
export type ChapterMoveDirection = typeof CHAPTER_MOVE_DIRECTIONS[number];

export function bookChapterDetailUrl(projectId: string, chapterId: string, tab = "content"): string {
  const workspaceTab = ({
    content: "drafts",
    more: "structure",
    sources: "stories",
  } as const)[tab as "content" | "more" | "sources"];
  return pageUrl("/pages/chapter-workspace/index", {
    projectId,
    chapterId,
    tab: workspaceTab,
  });
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

export function toggleBookChapterSourcesPatch(
  data: Pick<BookData, "chapters" | "stage" | "sourceCoverage">,
  chapterId: string,
): Partial<BookData> {
  const chapters = data.chapters.map((chapter) => (
    chapter.id === chapterId
      ? { ...chapter, sourceListExpanded: !chapter.sourceListExpanded }
      : chapter
  ));
  return chapterCollectionData(chapters, data.stage, data.sourceCoverage);
}
