import type { MemoirDraftProgress, MemoirStoryGrowthEvent, MemoirStorySnapshot } from "../../domain/memoir-snapshots";
import { storyViewData } from "../../components/story-tree/story-view-adapter";
import { STORY_GROWTH_NOTICE_IMAGES } from "../../components/story-tree/story-growth-stage";
import { emptyStoryViewData, type StoryViewData } from "../../components/story-tree/story-view-data";

export interface StoryGrowthTarget { chapterId: string; sectionId?: string }

interface StoryNotice {
  reviewNoticeText: string;
  reviewNoticeDetail: string;
  reviewNoticeAction: "issues" | "transcripts" | "book" | "";
}

export interface HomeStoryData extends StoryNotice {
  candidateNoticeText: string;
  candidateChapterId: string;
  story: StoryViewData;
  storyLoaded: boolean;
  hasStories: boolean;
  storyError: string;
  progress: MemoirDraftProgress | null;
  bookReadable: boolean;
  growthEvents: MemoirStoryGrowthEvent[];
  growthNoticeTitle: string;
  growthNoticeText: string;
  growthNoticeIcon: string;
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
    reviewNoticeText: "", reviewNoticeDetail: "", reviewNoticeAction: "",
    candidateNoticeText: "", candidateChapterId: "",
    story: emptyStoryViewData(), storyLoaded: false, hasStories: false, storyError: "", progress: null,
    bookReadable: false, growthEvents: [], growthNoticeTitle: "", growthNoticeText: "", growthNoticeIcon: "",
    growthTarget: null, narrationSaved: false, dismissingGrowth: false, retryingDraft: false,
  };
}

function namedNewSections(labels: string[]): string {
  const visible = labels.slice(0, 3).map(label => `《${label}》`).join("、");
  if (!visible) return "";
  return `：${visible}${labels.length > 3 ? "等" : ""}`;
}

export function homeStorySnapshotData(
  snapshot: MemoirStorySnapshot,
  _previous: StoryViewData,
  freshEventIds: Set<string> = new Set(),
): Partial<HomeStoryData> {
  const story = storyViewData(snapshot);
  const candidates = snapshot.chapters.filter(chapter => chapter.include_in_book && chapter.candidate_job_id);
  const chapters = new Map(story.chapters.map(chapter => [chapter.id, chapter]));
  const newChapters = new Set<string>(), newSections = new Set<string>();
  const updatedChapters = new Set<string>(), updatedSections = new Set<string>();
  const newSectionLabels = new Map<string, string>();
  const sectionParents = new Set<string>();
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
        (added ? newSections : updatedSections).add(section.id);
        if (added) newSectionLabels.set(
          section.id,
          chapter.title === section.title ? section.title : `${chapter.title} · ${section.title}`,
        );
        sectionParents.add(chapter.id);
        section.growthLabel = added || section.growthLabel === "新长出" ? "新长出" : "已补充";
        section.highlighted = true;
        section.growthAnimating ||= freshEventIds.has(event.event_id);
        chapter.growthLabel = added || chapter.growthLabel === "新长出" ? "新长出" : "已补充";
        chapter.growthAnimating ||= freshEventIds.has(event.event_id);
        growthTarget ||= { chapterId: chapter.id, sectionId: section.id };
      } else {
        (added ? newChapters : updatedChapters).add(chapter.id);
        chapter.growthLabel = added || chapter.growthLabel === "新长出" ? "新长出" : "已更新";
        chapter.growthAnimating ||= freshEventIds.has(event.event_id);
        growthTarget ||= { chapterId: chapter.id };
      }
      chapter.highlighted = true;
    }
  }
  for (const chapter of story.chapters) {
    if (chapter.highlighted && !chapter.growthLabel) chapter.growthLabel = "有更新";
  }
  const addedCount = newChapters.size + newSections.size;
  const updatedCount = [...updatedSections].filter(id => !newSections.has(id)).length
    + [...updatedChapters].filter(id => !newChapters.has(id) && !sectionParents.has(id)).length;
  const summary = [
    newChapters.size ? `新长出 ${newChapters.size} 个主题` : "",
    newSections.size ? `新长出 ${newSections.size} 个故事${namedNewSections([...newSectionLabels.values()])}` : "",
    updatedCount ? `补充了 ${updatedCount} 个已有故事` : "",
    bookUpdated ? "书稿已更新" : "",
  ].filter(Boolean).join("，");
  const growthNoticeKind = addedCount ? "new" : updatedCount ? "enriched" : "progress";
  return {
    ...homeStoryNotice(snapshot),
    candidateNoticeText: candidates.length ? `有 ${candidates.length} 个主题整理出了新稿，原稿已保留` : "",
    candidateChapterId: candidates[0]?.id || "",
    story, storyLoaded: true, hasStories: snapshot.chapters.length > 0, storyError: "", progress: snapshot.progress,
    bookReadable: snapshot.book_readable, growthEvents: snapshot.growth_events, growthTarget,
    growthNoticeTitle: addedCount ? "故事树长出了新内容" : updatedCount ? "回忆变得更完整了" : "故事整理有了新进展",
    growthNoticeText: summary || snapshot.growth_events.map(event => event.summary_text).join("\n"),
    growthNoticeIcon: STORY_GROWTH_NOTICE_IMAGES[growthNoticeKind],
  };
}

export function clearStoryGrowthAnimation(story: StoryViewData): StoryViewData {
  return { ...story, chapters: story.chapters.map(chapter => ({
    ...chapter, growthAnimating: false,
    sections: chapter.sections.map(section => ({ ...section, growthAnimating: false })),
  })) };
}


export function homeStoryNotice(snapshot: MemoirStorySnapshot): StoryNotice {
  const chapterReviews = snapshot.chapters.reduce(
    (total, chapter) => total + (chapter.issues?.review_count || 0),
    0,
  );
  const chapterText = chapterReviews ? `${chapterReviews} 个故事等您确认` : "";
  const bookText = snapshot.book_issues?.review_count
    ? `书里有 ${snapshot.book_issues.review_count} 处等您确认`
    : snapshot.book_issues?.supplement_count
      ? `书里有 ${snapshot.book_issues.supplement_count} 处可以继续聊`
      : "";
  if (chapterText || bookText) return {
    reviewNoticeText: chapterText || bookText,
    reviewNoticeDetail: chapterText && bookText ? bookText : "",
    reviewNoticeAction: "issues",
  };
  const materials = snapshot.progress?.needs_user_material_count || 0;
  if (materials) return {
    reviewNoticeText: `有 ${materials} 段回忆想请您看看`, reviewNoticeDetail: "", reviewNoticeAction: "transcripts",
  };
  if (snapshot.book_manual_edit_preserved) return {
    reviewNoticeText: "已保留您修改的稿件", reviewNoticeDetail: "", reviewNoticeAction: "book",
  };
  // Finished/closed issues disappear even while an older generation job still
  // reports needs_input or omitted chapters. Those job records are not tasks.
  return { reviewNoticeText: "", reviewNoticeDetail: "", reviewNoticeAction: "" };
}
