import type { MemoirStorySnapshot } from "../../domain/memoir-snapshots";

export type StoryTreeGrowthStage = 1 | 2 | 3 | 4 | 5;
export const STORY_TREE_STAGE_IMAGES: Record<StoryTreeGrowthStage, string> = {
  1: "/assets/story-tree/tree-hero-stage-01-seed.jpg",
  2: "/assets/story-tree/tree-hero-stage-02-seedling.jpg",
  3: "/assets/story-tree/tree-hero-stage-03-sapling.jpg",
  4: "/assets/story-tree/tree-hero-stage-04-branching.jpg",
  5: "/assets/story-tree/tree-hero-stage-05-mature.jpg",
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
