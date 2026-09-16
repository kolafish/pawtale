import type { MemoirStorySnapshot } from "../../domain/memoir-snapshots";

export type StoryTreeGrowthStage = 1 | 2 | 3 | 4 | 5;
export type StoryGrowthNoticeKind = "new" | "enriched" | "progress";

export const STORY_TREE_STAGE_IMAGES: Record<StoryTreeGrowthStage, string> = {
  1: "/assets/story-tree/tree-stage-01-seedling.png",
  2: "/assets/story-tree/tree-stage-02-sapling.png",
  3: "/assets/story-tree/tree-stage-03-branching.png",
  4: "/assets/story-tree/tree-stage-04-leafy.png",
  5: "/assets/story-tree/tree-stage-05-mature.png",
};

export const STORY_CHAPTER_SPRIG_IMAGE = "/assets/story-tree/chapter-sprig.png";

export const STORY_GROWTH_NOTICE_IMAGES: Record<StoryGrowthNoticeKind, string> = {
  new: STORY_CHAPTER_SPRIG_IMAGE,
  enriched: "/assets/story-tree/notice-enriched.png",
  progress: STORY_TREE_STAGE_IMAGES[3],
};

type StoryGrowthInput = Pick<
  MemoirStorySnapshot,
  "chapters" | "book_readable" | "book_char_count" | "book_chapter_count"
>;

export function resolveStoryTreeGrowthStage(snapshot: StoryGrowthInput): StoryTreeGrowthStage {
  const chapters = snapshot.chapters;
  if (!chapters.length) return 1;

  const effectiveChars = Math.max(
    snapshot.book_char_count || 0,
    chapters.reduce((total, chapter) => total + (chapter.char_count || 0), 0),
  );
  const readableChapterCount = chapters.filter(chapter => chapter.readable).length;
  const includedChapters = chapters.filter(chapter => chapter.include_in_book);
  const includedReadableCount = includedChapters.filter(chapter => chapter.readable).length;
  const fullBookCoversCurrentChapters = includedChapters.length > 0
    && snapshot.book_readable
    && (snapshot.book_chapter_count || 0) === includedChapters.length
    && includedReadableCount === includedChapters.length;

  if (fullBookCoversCurrentChapters && (includedChapters.length >= 3 || effectiveChars >= 10_000)) return 5;
  if (
    chapters.length >= 5
    || effectiveChars >= 10_000
    || (includedChapters.length >= 3 && includedReadableCount * 3 >= includedChapters.length * 2)
    || fullBookCoversCurrentChapters
  ) return 4;
  if (chapters.length >= 3 || effectiveChars >= 3_000 || readableChapterCount >= 2) return 3;
  return 2;
}
