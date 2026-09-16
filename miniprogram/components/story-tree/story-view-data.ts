import { STORY_TREE_STAGE_IMAGES, type StoryTreeGrowthStage } from "./story-growth-stage";

export type StoryChapterState = "settled" | "attention" | "draft" | "muted";

export interface StoryMemoirSummaryView {
  title: string;
  progressDetail: string;
}

export interface StorySectionView {
  id: string;
  title: string;
  materialText: string;
  imageCount: number;
  videoCount: number;
  highlighted: boolean;
  growthLabel: string;
  growthAnimating: boolean;
}

export interface StoryChapterView {
  detailActionText: string;
  id: string;
  ordinal: number;
  hasSections: boolean;
  title: string;
  timeRangeText: string;
  summaryText: string;
  outcomeText: string;
  readable: boolean;
  imageCount: number;
  videoCount: number;
  state: StoryChapterState;
  expanded: boolean;
  highlighted: boolean;
  growthLabel: string;
  growthAnimating: boolean;
  sections: StorySectionView[];
}

export interface StoryOverviewView {
  readableText: string;
  bookText: string;
  title: string;
  summaryText: string;
  outcomeText: string;
  growthStage: StoryTreeGrowthStage;
  growthImageSrc: string;
}

export interface StoryViewData {
  memoir: StoryMemoirSummaryView;
  overview: StoryOverviewView;
  chapters: StoryChapterView[];
  unassignedMaterialTitle: string;
  unassignedMaterialText: string;
  unassignedMaterialDetail: string;
  unassignedImageCount: number;
  unassignedVideoCount: number;
}

export function emptyStoryViewData(): StoryViewData {
  return {
    memoir: {
      title: "我的故事",
      progressDetail: "查看书稿",
    },
    overview: {
      readableText: "还没有正文",
      bookText: "",
      title: "人生总览",
      summaryText: "0 章 · 0 小节 · 0 段讲述",
      outcomeText: "还没有正文",
      growthStage: 1,
      growthImageSrc: STORY_TREE_STAGE_IMAGES[1],
    },
    chapters: [],
    unassignedMaterialTitle: "从一段讲述开始",
    unassignedMaterialText: "还没有讲述",
    unassignedMaterialDetail: "讲下第一段回忆后，它会从这里长成故事",
    unassignedImageCount: 0,
    unassignedVideoCount: 0,
  };
}

export function toggleStoryChapter(
  chapters: StoryChapterView[],
  chapterId: string,
): StoryChapterView[] {
  return chapters.map((chapter) => (
    chapter.id === chapterId
      ? { ...chapter, expanded: !chapter.expanded }
      : chapter
  ));
}
