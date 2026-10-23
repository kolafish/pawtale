import type { MemoirStorySnapshot } from "../../domain/memoir-snapshots";
import { memoirHomepageTitle } from "../../services/memoir-display-copy";
import type { StoryTone, StoryViewData } from "./story-view-data";

const STORY_TONES: StoryTone[] = ["clay", "sun", "leaf"];

function formatBookChars(count: number): string {
  if (count >= 10_000) {
    const value = (count / 10_000).toFixed(1).replace(/\.0$/, "");
    return `约 ${value} 万字`;
  }
  return count ? `${count} 字` : "";
}

/** Home presentation data; a visitor's sample memoir supplies the same snapshot fields. */
export type StoryViewSource = Pick<MemoirStorySnapshot, "chapters" | "book_chapter_count" | "book_char_count"> & {
  identity: { active_project: { title: string } | null };
  progress?: { material_count: number } | null;
};

export function storyViewData(snapshot: StoryViewSource): StoryViewData {
  const project = snapshot.identity.active_project;
  const chapters = snapshot.chapters.map((chapter, index) => {
    return {
      id: chapter.id,
      hasSections: chapter.sections.length > 0,
      title: chapter.title || "未命名章节",
      storyCountText: chapter.sections.length ? `${chapter.sections.length} 小节` : "正在整理小节",
      readable: chapter.readable,
      state: chapter.state,
      tone: STORY_TONES[index % STORY_TONES.length],
      highlighted: false,
      growthLabel: "",
      growthAnimating: false,
      sections: chapter.sections.map((section) => ({
        id: section.id,
        title: section.title || "未命名小节",
        highlighted: false,
        growthLabel: "",
        growthAnimating: false,
      })),
    };
  });
  const chapterCount = snapshot.book_chapter_count || chapters.length;
  const bookChars = snapshot.book_char_count
    || snapshot.chapters.reduce((total, chapter) => total + chapter.char_count, 0);
  const bookCharText = formatBookChars(bookChars);
  const storyCount = chapters.reduce((total, chapter) => total + chapter.sections.length, 0);
  const materialCount = snapshot.progress?.material_count
    || snapshot.chapters.reduce((total, chapter) => total + chapter.material_count, 0);
  // Current included chapters supply all three counters, independently of the last assembled book version.
  const includedChapters = snapshot.chapters.filter(chapter => chapter.include_in_book);
  const includedSections = includedChapters.reduce((total, chapter) => total + chapter.sections.length, 0);
  const includedChars = includedChapters.reduce((total, chapter) => total + chapter.char_count, 0);
  return {
    memoir: {
      title: project?.title || "我的故事",
      bookMetaText: [chapterCount ? `${chapterCount} 章` : "", bookCharText].filter(Boolean).join(" · ") || "正在整理",
      bookProgressText: [includedChapters.length ? `${includedChapters.length} 章` : "",
        includedSections ? `${includedSections} 小节` : "", formatBookChars(includedChars)]
        .filter(Boolean)
        .join(" · ") || "正在整理",
    },
    overview: {
      title: memoirHomepageTitle(project?.title || ""),
      summaryText: chapters.length
        ? `${chapters.length} 章 · ${storyCount} 小节`
        : materialCount ? "回忆正在整理成故事" : "故事树还在等待第一段回忆",
      themeTags: chapters.slice(0, 3).map((chapter) => ({
        id: chapter.id,
        title: chapter.title,
        tone: chapter.tone,
        readable: chapter.readable,
      })),
      hasChapters: chapters.length > 0,
      growthAnimating: false,
    },
    chapters,
  };
}
