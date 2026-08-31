export type StoryChapterTone = "clay" | "amber" | "sage" | "blue" | "stone";

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
  tone: StoryChapterTone;
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
  unassignedMaterialText: string;
}

export function emptyStoryViewData(): StoryViewData {
  return {
    memoir: {
      title: "我的故事",
      createdText: "",
      updatedText: "",
      progressLabel: "成书进度",
      progressValue: "0 章",
      progressDetail: "等待整理",
    },
    overview: {
      title: "人生总览",
      summaryText: "0 章 · 0 小节 · 0 段讲述",
      outcomeText: "还没有正文",
    },
    chapters: [],
    unassignedMaterialText: "还没有待整理的讲述",
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
