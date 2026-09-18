import type { MemoirStorySnapshot } from "../../domain/memoir-snapshots";
import { resolveStoryTreeGrowthStage, STORY_TREE_STAGE_IMAGES } from "./story-growth-stage";
import type { StoryTone, StoryViewData } from "./story-view-data";

const STORY_TONES: StoryTone[] = ["clay", "sun", "leaf"];

function formatBookChars(count: number): {
  value: string;
  unit: string;
  prefix: string;
  summary: string;
} {
  if (count >= 10_000) {
    const value = (count / 10_000).toFixed(1).replace(/\.0$/, "");
    return { value, unit: "万字", prefix: "约", summary: `约 ${value} 万字` };
  }
  return count
    ? { value: String(count), unit: "字", prefix: "", summary: `${count} 字` }
    : { value: "", unit: "字", prefix: "", summary: "" };
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
  const recentBookCharCountDelta = snapshot.growth_events.reduce(
    (total, event) => total + Math.max(0, event.book_char_count_delta || 0),
    0,
  );
  const storyCount = chapters.reduce((total, chapter) => total + chapter.sections.length, 0);
  const materialCount = snapshot.progress?.material_count
    || snapshot.chapters.reduce((total, chapter) => total + chapter.material_count, 0);
  const growthStage = resolveStoryTreeGrowthStage(snapshot);
  return {
    memoir: {
      title: project?.title || "我的故事",
      progressDetail: snapshot.book_readable ? "读读我的书" : "看看书稿",
      bookMetaText: chapterCount
        ? `已写成 ${chapterCount} 章${bookCharText.summary ? ` · ${bookCharText.summary}` : ""}`
        : "故事会在这里慢慢写成书",
      bookCharCountValue: bookCharText.value,
      bookCharCountUnit: bookCharText.unit,
      bookCharCountPrefix: bookCharText.prefix,
      bookChapterText: chapterCount ? `已写成 ${chapterCount} 章` : "故事正在慢慢写成书",
      bookRecentText: recentBookCharCountDelta ? `最近又增加了 ${recentBookCharCountDelta} 字` : "",
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
      growthAnimating: false,
    },
    chapters,
  };
}
