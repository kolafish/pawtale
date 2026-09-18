import { STORY_TREE_STAGE_IMAGES, type StoryTreeGrowthStage } from "./story-growth-stage";

export type StoryChapterState = "settled" | "attention" | "draft" | "muted";
export type StoryTone = "leaf" | "sun" | "clay";

export interface StoryMemoirSummaryView {
  title: string;
  progressDetail: string;
  bookMetaText: string;
  bookCharCountValue: string;
  bookCharCountUnit: string;
  bookCharCountPrefix: string;
  bookChapterText: string;
  bookRecentText: string;
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
      progressDetail: "看看书稿",
      bookMetaText: "故事会在这里慢慢写成书",
      bookCharCountValue: "",
      bookCharCountUnit: "字",
      bookCharCountPrefix: "",
      bookChapterText: "故事正在慢慢写成书",
      bookRecentText: "",
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
