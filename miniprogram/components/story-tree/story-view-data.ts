import { STORY_TREE_STAGE_IMAGES, type StoryTreeGrowthStage } from "./story-growth-stage";

export type StoryChapterState = "settled" | "attention" | "draft" | "muted";
export type StoryTone = "leaf" | "sun" | "clay";
export type StoryStatusTone = "rich" | "continue" | "starting" | "working" | "confirm" | "quiet";

export interface StoryMemoirSummaryView {
  title: string;
  progressDetail: string;
  bookMetaText: string;
}

export interface StoryThemeTagView {
  id: string;
  title: string;
  tone: StoryTone;
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
  statusText: string;
  statusTone: StoryStatusTone;
  readable: boolean;
  state: StoryChapterState;
  featured: boolean;
  tone: StoryTone;
  highlighted: boolean;
  growthLabel: string;
  growthAnimating: boolean;
  sections: StorySectionView[];
}

export interface StoryOverviewView {
  title: string;
  summaryText: string;
  growthText: string;
  growthStage: StoryTreeGrowthStage;
  growthImageSrc: string;
  themeTags: StoryThemeTagView[];
  hasChapters: boolean;
  hasMoreChapters: boolean;
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
      progressDetail: "看看书稿",
      bookMetaText: "故事会在这里慢慢写成书",
    },
    overview: {
      title: "我的故事树",
      summaryText: "故事树还在等待第一段回忆",
      growthText: "每一个故事，都从一颗种子开始。",
      growthStage: 1,
      growthImageSrc: STORY_TREE_STAGE_IMAGES[1],
      themeTags: [],
      hasChapters: false,
      hasMoreChapters: false,
    },
    chapters: [],
  };
}
