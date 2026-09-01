export type StoryChapterState = "settled" | "attention" | "draft" | "muted";

export interface StoryMemoirSummaryView {
  title: string;
  createdText: string;
  updatedText: string;
  progressLabel: string;
  progressValue: string;
  progressDetail: string;
}

export interface StorySectionView {
  id: string;
  title: string;
  materialText: string;
  imageCount: number;
  videoCount: number;
}

export interface StoryChapterView {
  id: string;
  title: string;
  timeRangeText: string;
  summaryText: string;
  outcomeText: string;
  imageCount: number;
  videoCount: number;
  state: StoryChapterState;
  expanded: boolean;
  sections: StorySectionView[];
}

export interface StoryOverviewView {
  title: string;
  summaryText: string;
  outcomeText: string;
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
      createdText: "",
      updatedText: "",
      progressLabel: "书稿",
      progressValue: "尚未生成",
      progressDetail: "开始整理",
    },
    overview: {
      title: "人生总览",
      summaryText: "0 章 · 0 小节 · 0 段讲述",
      outcomeText: "还没有正文",
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
