import type { MemoirStorySnapshot } from "../../domain/memoir-snapshots";

export type StoryTreeGrowthStage = 1 | 2 | 3 | 4 | 5;
export type StoryGrowthNoticeKind = "new" | "enriched" | "progress";

export const STORY_TREE_STAGE_IMAGES: Record<StoryTreeGrowthStage, string> = {
  1: "/assets/story-tree/tree-stage-01-seed.png",
  2: "/assets/story-tree/tree-stage-02-seedling.png",
  3: "/assets/story-tree/tree-stage-03-sapling.png",
  4: "/assets/story-tree/tree-stage-04-branching.png",
  5: "/assets/story-tree/tree-stage-05-mature.png",
};

export const STORY_CHAPTER_SPRIG_IMAGE = "/assets/story-tree/chapter-sprig.png";

export const STORY_GROWTH_NOTICE_IMAGES: Record<StoryGrowthNoticeKind, string> = {
  new: STORY_CHAPTER_SPRIG_IMAGE,
  enriched: "/assets/story-tree/notice-enriched.png",
  progress: STORY_TREE_STAGE_IMAGES[4],
};

type StoryGrowthInput = Pick<
  MemoirStorySnapshot,
  "chapters"
>;

export function resolveStoryTreeGrowthStage(snapshot: StoryGrowthInput): StoryTreeGrowthStage {
  const chapters = snapshot.chapters;
  if (!chapters.length) return 1;

  const currentChapterChars = chapters.reduce((total, chapter) => total + (chapter.char_count || 0), 0);
  if (chapters.length >= 5 && currentChapterChars >= 10_000) return 5;
  if (chapters.length >= 5 || currentChapterChars >= 10_000) return 4;
  if (chapters.length >= 3 || currentChapterChars >= 3_000) return 3;
  return 2;
}
