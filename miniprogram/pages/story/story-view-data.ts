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

function section(
  id: string,
  title: string,
  materialCount: number,
  imageCount = 0,
  videoCount = 0,
): StorySectionView {
  return {
    id,
    title,
    materialText: `${materialCount} 段讲述`,
    imageCount,
    videoCount,
  };
}

function chapter(
  id: string,
  title: string,
  timeRangeText: string,
  summaryText: string,
  outcomeText: string,
  tone: StoryChapterTone,
  sections: StorySectionView[],
  expanded = false,
): StoryChapterView {
  return {
    id,
    title,
    timeRangeText,
    summaryText,
    outcomeText,
    tone,
    sections,
    expanded,
  };
}

export function storyPreviewData(): StoryViewData {
  return {
    memoir: {
      title: "一路走来的故事",
      createdText: "创建于 2026 年春",
      updatedText: "更新于今天",
      progressLabel: "成书进度",
      progressValue: "2/5 章",
      progressDetail: "已经成稿",
    },
    overview: {
      title: "人生总览",
      summaryText: "5 章 · 14 小节 · 128 段讲述",
      outcomeText: "约 3.8 万字",
    },
    chapters: [
      chapter(
        "childhood", "童年时光", "1940—1952", "4 小节 · 42 段讲述", "已成稿", "clay",
        [
          section("family", "家乡与家人", 12, 3),
          section("school", "上学的日子", 14, 2, 1),
          section("people", "难忘的人", 8, 1),
          section("other-childhood", "其他回忆", 8),
        ],
        true,
      ),
      chapter(
        "youth", "青年岁月", "1953—1975", "3 小节 · 31 段讲述", "已成稿", "amber",
        [
          section("leaving-home", "离开家乡", 9, 1),
          section("first-job", "最初的工作", 13, 2),
          section("youth-friends", "青春伙伴", 9, 1, 1),
        ],
      ),
      chapter(
        "work", "工作与奋斗", "1976—2000", "3 小节 · 28 段讲述", "整理中", "sage",
        [
          section("work-turning-point", "工作转折", 10, 2),
          section("workmates", "并肩的人", 11, 1),
          section("work-reflection", "回头来看", 7),
        ],
      ),
      chapter(
        "family-life", "家庭生活", "1980—2015", "2 小节 · 19 段讲述", "待补充", "blue",
        [
          section("family-days", "一家人的日子", 12, 4, 1),
          section("children", "陪伴孩子长大", 7, 2),
        ],
      ),
      chapter(
        "later-years", "晚年时光", "2016—至今", "2 小节 · 8 段讲述", "刚刚开始", "stone",
        [
          section("retirement", "退休后的生活", 5, 1),
          section("words-for-family", "想对家人说", 3),
        ],
      ),
    ],
    unassignedMaterialText: "还有 6 段讲述等待整理",
  };
}

export function togglePreviewChapter(
  chapters: StoryChapterView[],
  chapterId: string,
): StoryChapterView[] {
  return chapters.map((chapter) => (
    chapter.id === chapterId
      ? { ...chapter, expanded: !chapter.expanded }
      : chapter
  ));
}
