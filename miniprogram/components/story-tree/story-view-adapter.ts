import type { MemoirStoryChapter, MemoirStorySnapshot } from "../../domain/memoir-snapshots";
import { resolveStoryTreeGrowthStage, STORY_TREE_STAGE_IMAGES } from "./story-growth-stage";
import type { StoryStatusTone, StoryTone, StoryViewData } from "./story-view-data";

const STORY_TONES: StoryTone[] = ["clay", "sun", "leaf"];

function formatBookChars(count: number): string {
  if (count >= 10_000) return `约 ${(count / 10_000).toFixed(1)} 万字`;
  return count ? `${count} 字` : "";
}

function storyStatus(chapter: MemoirStoryChapter): { text: string; tone: StoryStatusTone } {
  if (chapter.issues?.review_count) return { text: "待您确认", tone: "confirm" };
  if (chapter.candidate_job_id) return { text: "有新稿", tone: "confirm" };
  if (chapter.issues?.supplement_count) return { text: "可继续聊", tone: "continue" };
  if (chapter.state === "draft") return { text: "正在整理", tone: "working" };
  if (chapter.material_count <= 1 && chapter.sections.length <= 1) return { text: "刚开始", tone: "starting" };
  if (chapter.state === "settled" || chapter.readable) return { text: "已丰富", tone: "rich" };
  return { text: "已整理", tone: "quiet" };
}

function storyGrowthText(snapshot: MemoirStorySnapshot, growthStage: number): string {
  if (!snapshot.chapters.length) return "每一个故事，都从一颗种子开始。";
  const supplementCount = snapshot.chapters.reduce(
    (total, chapter) => total + (chapter.issues?.supplement_count || 0),
    0,
  );
  if (supplementCount) return `已经很丰富了，还有 ${supplementCount} 个故事可以继续聊聊。`;
  const reviewCount = snapshot.chapters.reduce(
    (total, chapter) => total + (chapter.issues?.review_count || 0),
    0,
  );
  if (reviewCount) return `故事正在慢慢长大，还有 ${reviewCount} 处想请您确认。`;
  if ((snapshot.progress?.pending_material_count || 0) > 0) return "刚讲过的回忆，正在慢慢长进故事树里。";
  if (growthStage === 2) return "故事已经发芽，慢慢讲，它会继续长大。";
  if (growthStage === 3) return "故事树正在舒展，想起什么都可以继续讲。";
  return "您的故事树已经很丰富了，还可以继续慢慢讲。";
}

export function storyViewData(snapshot: MemoirStorySnapshot): StoryViewData {
  const project = snapshot.identity.active_project;
  const chapters = snapshot.chapters.map((chapter, index) => {
    const status = storyStatus(chapter);
    return {
      id: chapter.id,
      hasSections: chapter.sections.length > 0,
      title: chapter.title || "未命名故事",
      memoryText: `${chapter.material_count} 段回忆`,
      statusText: status.text,
      statusTone: status.tone,
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
      growthText: storyGrowthText(snapshot, growthStage),
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
