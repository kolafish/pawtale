import type { MemoirDraftProgress, MemoirStoryGrowthEvent, MemoirStorySnapshot } from "../../domain/memoir-snapshots";
import { storyViewData } from "../../components/story-tree/story-view-adapter";
import { emptyStoryViewData, type StoryViewData } from "../../components/story-tree/story-view-data";

export interface StoryGrowthTarget { chapterId: string; sectionId?: string }

export interface HomeStoryData {
  story: StoryViewData;
  storyLoaded: boolean;
  hasStories: boolean;
  storyError: string;
  progress: MemoirDraftProgress | null;
  growthEvents: MemoirStoryGrowthEvent[];
  growthNoticeTitle: string;
  growthNoticeText: string;
  growthNoticeContext: string;
  growthTarget: StoryGrowthTarget | null;
  narrationSaved: boolean;
  dismissingGrowth: boolean;
  retryingDraft: boolean;
}

/** Viewing a snapshot never acknowledges server events. Initial content stays still. */
export class HomeGrowthTracker {
  private scope = "";
  private seen = new Set<string>();

  observe(userId: string, projectId: string, events: MemoirStoryGrowthEvent[]): Set<string> {
    const scope = `${userId}/${projectId}`;
    const firstSnapshot = this.scope !== scope;
    if (firstSnapshot) { this.scope = scope; this.seen.clear(); }
    const fresh = new Set<string>();
    for (const event of events) {
      if (!firstSnapshot && !this.seen.has(event.event_id)) fresh.add(event.event_id);
      this.seen.add(event.event_id);
    }
    return fresh;
  }

  reset(): void { this.scope = ""; this.seen.clear(); }
}

export function emptyHomeStoryData(): HomeStoryData {
  return {
    story: emptyStoryViewData(), storyLoaded: false, hasStories: false, storyError: "", progress: null,
    growthEvents: [], growthNoticeTitle: "", growthNoticeText: "", growthNoticeContext: "",
    growthTarget: null, narrationSaved: false,
    dismissingGrowth: false, retryingDraft: false,
  };
}

export function homeStorySnapshotData(
  snapshot: MemoirStorySnapshot,
  _previous: StoryViewData,
  freshEventIds: Set<string> = new Set(),
): Partial<HomeStoryData> {
  const story = storyViewData(snapshot);
  const chapters = new Map(story.chapters.map(chapter => [chapter.id, chapter]));
  const newChapters = new Set<string>(), newSections = new Set<string>();
  const updatedChapters = new Set<string>(), updatedSections = new Set<string>();
  const sectionParents = new Set<string>();
  const changedChapterTitles = new Set<string>();
  let bookUpdated = false;
  let growthTarget: StoryGrowthTarget | null = null;
  for (const event of snapshot.growth_events) {
    for (const change of event.changes) {
      if (change.kind === "book_updated") { bookUpdated = true; continue; }
      const chapter = chapters.get(change.chapter_id);
      if (!chapter) continue;
      const added = change.kind === "chapter_added" || change.kind === "section_added";
      if ("section_id" in change) {
        const section = chapter.sections.find(item => item.id === change.section_id);
        if (!section) continue;
        changedChapterTitles.add(chapter.title);
        (added ? newSections : updatedSections).add(section.id);
        sectionParents.add(chapter.id);
        section.growthLabel = added || section.growthLabel === "新长出" ? "新长出" : "已补充";
        section.highlighted = true;
        section.growthAnimating ||= freshEventIds.has(event.event_id);
        if (added) chapter.growthLabel = "新长出";
        chapter.growthAnimating ||= freshEventIds.has(event.event_id);
        growthTarget ||= { chapterId: chapter.id, sectionId: section.id };
      } else {
        changedChapterTitles.add(chapter.title);
        (added ? newChapters : updatedChapters).add(chapter.id);
        if (added) chapter.growthLabel = "新长出";
        chapter.growthAnimating ||= freshEventIds.has(event.event_id);
        growthTarget ||= { chapterId: chapter.id };
      }
      chapter.highlighted = true;
    }
  }
  const addedCount = newChapters.size + newSections.size;
  const updatedCount = [...updatedSections].filter(id => !newSections.has(id)).length
    + [...updatedChapters].filter(id => !newChapters.has(id) && !sectionParents.has(id)).length;
  const bookCharCountDelta = snapshot.growth_events.reduce(
    (total, event) => total + Math.max(0, event.book_char_count_delta || 0),
    0,
  );
  const growthMetrics = [
    newChapters.size ? `新增 ${newChapters.size} 个主题` : "",
    newSections.size ? `${newChapters.size ? "" : "新增 "}${newSections.size} 个故事` : "",
    updatedCount ? `补充 ${updatedCount} 个已有故事` : "",
    bookCharCountDelta ? `书稿 +${bookCharCountDelta} 字` : "",
  ].filter(Boolean);
  const fallbackText = snapshot.growth_events.map(event => event.summary_text).filter(Boolean).join(" · ");
  const growthNoticeText = growthMetrics.slice(0, 2).join(" · ")
    || (bookUpdated ? "书稿已更新" : fallbackText);
  const chapterTitles = [...changedChapterTitles];
  const growthNoticeContext = chapterTitles.length
    ? `${chapterTitles[0]}等内容已更新`
    : "";
  story.overview.growthAnimating = freshEventIds.size > 0
    && Boolean(addedCount || updatedCount || bookCharCountDelta);
  return {
    story, storyLoaded: true, hasStories: snapshot.chapters.length > 0, storyError: "", progress: snapshot.progress,
    growthEvents: snapshot.growth_events, growthTarget,
    growthNoticeTitle: addedCount || bookCharCountDelta
      ? "您的书又长了一点"
      : updatedCount ? "这段回忆刚刚又丰富了一些" : "故事整理有了新进展",
    growthNoticeText,
    growthNoticeContext,
  };
}

export function clearStoryGrowthAnimation(story: StoryViewData): StoryViewData {
  return { ...story, overview: { ...story.overview, growthAnimating: false }, chapters: story.chapters.map(chapter => ({
    ...chapter, growthAnimating: false,
    sections: chapter.sections.map(section => ({ ...section, growthAnimating: false })),
  })) };
}
