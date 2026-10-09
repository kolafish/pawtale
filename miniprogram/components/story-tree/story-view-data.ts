import { STORY_TREE_STAGE_IMAGES, type StoryTreeGrowthStage } from "./story-growth-stage";

export type StoryChapterState = "settled" | "attention" | "draft" | "muted";
export type StoryTone = "leaf" | "sun" | "clay";

export interface StoryMemoirSummaryView {
  title: string;
  bookMetaText: string;
}

export interface StoryThemeTagView {
  id: string;
  title: string;
  tone: StoryTone;
  readable: boolean;
}

export interface StorySectionView {
  id: string;
  title: string;
  highlighted: boolean;
  growthLabel: string;
  growthAnimating: boolean;
}

export interface StoryChapterView {
  id: string;
  hasSections: boolean;
  title: string;
  storyCountText: string;
  readable: boolean;
  state: StoryChapterState;
  tone: StoryTone;
  highlighted: boolean;
  growthLabel: string;
  growthAnimating: boolean;
  sections: StorySectionView[];
}

export interface StoryOverviewView {
  title: string;
  summaryText: string;
  growthStage: StoryTreeGrowthStage;
  growthImageSrc: string;
  themeTags: StoryThemeTagView[];
  hasChapters: boolean;
  growthAnimating: boolean;
}

export interface StoryViewData {
  memoir: StoryMemoirSummaryView;
  overview: StoryOverviewView;
  chapters: StoryChapterView[];
}

export function emptyStoryViewData(): StoryViewData {
  return {
    memoir: {
      title: "我的故事",
      bookMetaText: "正在整理",
    },
    overview: {
      title: "我的故事树",
      summaryText: "故事树还在等待第一段回忆",
      growthStage: 1,
      growthImageSrc: STORY_TREE_STAGE_IMAGES[1],
      themeTags: [],
      hasChapters: false,
      growthAnimating: false,
    },
    chapters: [],
  };
}
