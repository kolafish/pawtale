import type { MemoirStorySnapshot } from "../../domain/memoir-snapshots";
import { resolveStoryTreeGrowthStage, STORY_TREE_STAGE_IMAGES } from "./story-growth-stage";
import type { StoryTone, StoryViewData } from "./story-view-data";

const STORY_TONES: StoryTone[] = ["clay", "sun", "leaf"];

function formatBookChars(count: number): string {
  if (count >= 10_000) return `约 ${(count / 10_000).toFixed(1)} 万字`;
  return count ? `${count} 字` : "";
}

export function storyViewData(snapshot: MemoirStorySnapshot): StoryViewData {
  const project = snapshot.identity.active_project;
  const chapters = snapshot.chapters.map((chapter, index) => {
    return {
      id: chapter.id,
      hasSections: chapter.sections.length > 0,
      title: chapter.title || "未命名故事",
      storyCountText: chapter.sections.length ? `${chapter.sections.length} 个故事` : "正在长成故事",
      readable: chapter.readable,
      state: chapter.state,
      featured: index < 3,
      tone: STORY_TONES[index % STORY_TONES.length],
      highlighted: false,
      growthLabel: "",
      growthAnimating: false,
      sections: chapter.sections.map((section) => ({
        id: section.id,
        title: section.title || "未命名故事",
        highlighted: false,
        growthLabel: "",
        growthAnimating: false,
      })),
    };
  });
  const readableCount = chapters.filter(chapter => chapter.readable).length;
  const chapterCount = snapshot.book_chapter_count || readableCount;
  const bookChars = snapshot.book_char_count
    || snapshot.chapters.reduce((total, chapter) => total + chapter.char_count, 0);
  const bookCharText = formatBookChars(bookChars);
  const storyCount = chapters.reduce((total, chapter) => total + chapter.sections.length, 0);
  const materialCount = snapshot.progress?.material_count
    || snapshot.chapters.reduce((total, chapter) => total + chapter.material_count, 0);
  const growthStage = resolveStoryTreeGrowthStage(snapshot);
  return {
    memoir: {
      title: project?.title || "我的故事",
      progressDetail: snapshot.book_readable ? "读读我的书" : "看看书稿",
      bookMetaText: chapterCount
        ? `已写成 ${chapterCount} 章${bookCharText ? ` · ${bookCharText}` : ""}`
        : "故事会在这里慢慢写成书",
    },
    overview: {
      title: "我的故事树",
      summaryText: chapters.length
        ? `${chapters.length} 个主题 · ${storyCount} 个故事`
        : materialCount ? "回忆正在整理成故事" : "故事树还在等待第一段回忆",
      growthStage,
      growthImageSrc: STORY_TREE_STAGE_IMAGES[growthStage],
      themeTags: chapters.slice(0, 3).map((chapter) => ({
        id: chapter.id,
        title: chapter.title,
        tone: chapter.tone,
      })),
      hasChapters: chapters.length > 0,
      hasMoreChapters: chapters.length > 3,
    },
    chapters,
  };
}
