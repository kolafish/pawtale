import type { MemoirDraftProgress, MemoirStoryGrowthEvent, MemoirStorySnapshot } from "../../domain/memoir-snapshots";
import { storyViewData } from "../../components/story-tree/story-view-adapter";
import { emptyStoryViewData, type StoryViewData } from "../../components/story-tree/story-view-data";
import type { ShareLikeNotice } from "../../services/share-likes";

export type HomeNotice =
  | { id: string; kind: "growth"; text: string; eventId: string }
  | { id: string; kind: "progress"; text: string; action: "retry" | "review" | "supplement" | "" }
  | { id: string; kind: "saved"; text: string }
  | { id: string; kind: "like"; text: string; people: ShareLikeNotice };

const PROGRESS_NOTICE_KEY = "pawtale_home_progress_notice_v1";

function progressNoticeKey(userId: string, projectId: string): string {
  return `${PROGRESS_NOTICE_KEY}:${userId}:${projectId}`;
}

export function readDismissedProgressNotice(userId: string, projectId: string): string {
  try {
    const value: unknown = wx.getStorageSync(progressNoticeKey(userId, projectId));
    return typeof value === "string" ? value : "";
  } catch { return ""; }
}

export function rememberDismissedProgressNotice(userId: string, projectId: string, id: string): void {
  try { wx.setStorageSync(progressNoticeKey(userId, projectId), id); }
  catch { /* The status can be shown again if storage is unavailable. */ }
}

export interface HomeStoryData {
  story: StoryViewData;
  storyLoaded: boolean;
  hasStories: boolean;
  storyError: string;
  progress: MemoirDraftProgress | null;
  progressNoticeRevision: string;
  growthNotices: HomeNotice[];
  narrationSaved: boolean;
  dismissingGrowth: boolean;
  retryingDraft: boolean;
}

/** Viewing a snapshot never acknowledges server events. Initial content stays still. */
export class HomeGrowthTracker {
  private scope = "";
  private seen = new Set<string>();
  private acknowledged = new Set<string>();
  private queued = new Map<string, MemoirStoryGrowthEvent>();

  observe(userId: string, projectId: string, events: MemoirStoryGrowthEvent[]): Set<string> {
    const scope = `${userId}/${projectId}`;
    const firstSnapshot = this.scope !== scope;
    if (firstSnapshot) { this.scope = scope; this.seen.clear(); this.acknowledged.clear(); this.queued.clear(); }
    const fresh = new Set<string>();
    for (const event of events) {
      if (!firstSnapshot && !this.seen.has(event.event_id)) fresh.add(event.event_id);
      this.seen.add(event.event_id);
    }
    return fresh;
  }

  acknowledge(eventId: string): void {
    this.acknowledged.add(eventId);
    this.queued.delete(eventId);
  }

  invalidate(eventId: string): void {
    this.queued.delete(eventId);
    this.seen.delete(eventId);
  }

  pending(events: MemoirStoryGrowthEvent[]): MemoirStoryGrowthEvent[] {
    // Snapshot history is bounded; keep messages already queued until the user reads them.
    for (const event of events) {
      if (!this.acknowledged.has(event.event_id)) this.queued.set(event.event_id, event);
    }
    return [...this.queued.values()];
  }

  reset(): void { this.scope = ""; this.seen.clear(); this.acknowledged.clear(); this.queued.clear(); }
}

export function emptyHomeStoryData(): HomeStoryData {
  return {
    story: emptyStoryViewData(), storyLoaded: false, hasStories: false, storyError: "", progress: null, progressNoticeRevision: "",
    growthNotices: [], narrationSaved: false,
    dismissingGrowth: false, retryingDraft: false,
  };
}

export function homeStorySnapshotData(
  snapshot: MemoirStorySnapshot,
  freshEventIds: Set<string> = new Set(),
): Partial<HomeStoryData> {
  const story = storyViewData(snapshot);
  const chapters = new Map(story.chapters.map(chapter => [chapter.id, chapter]));
  let freshGrowth = false;
  for (const event of snapshot.growth_events) {
    for (const change of event.changes) {
      if (change.kind === "book_updated") continue;
      const chapter = chapters.get(change.chapter_id);
      if (!chapter) continue;
      const added = change.kind === "chapter_added" || change.kind === "section_added";
      if ("section_id" in change) {
        const section = chapter.sections.find(item => item.id === change.section_id);
        if (!section) continue;
        section.growthLabel = added || section.growthLabel === "新长出" ? "新长出" : "已补充";
        section.highlighted = true;
        section.growthAnimating ||= freshEventIds.has(event.event_id);
        if (added) chapter.growthLabel = "新长出";
        chapter.growthAnimating ||= freshEventIds.has(event.event_id);
      } else {
        if (added) chapter.growthLabel = "新长出";
        chapter.growthAnimating ||= freshEventIds.has(event.event_id);
      }
      chapter.highlighted = true;
      freshGrowth ||= freshEventIds.has(event.event_id);
    }
  }
  story.overview.growthAnimating = freshGrowth || snapshot.growth_events.some(event => (
    freshEventIds.has(event.event_id) && event.book_char_count_delta > 0
  ));
  return {
    story, storyLoaded: true, hasStories: snapshot.chapters.length > 0, storyError: "", progress: snapshot.progress,
    progressNoticeRevision: snapshot.updated_at,
    growthNotices: [...snapshot.growth_events]
      .sort((a, b) => a.created_at.localeCompare(b.created_at))
      .map(event => growthNotice(event, story)),
  };
}

function growthNotice(event: MemoirStoryGrowthEvent, story: StoryViewData): HomeNotice {
  const addedChapters = new Set<string>(), addedSections = new Set<string>();
  for (const change of event.changes) {
    if (change.kind === "book_updated") continue;
    const chapter = story.chapters.find(item => item.id === change.chapter_id);
    if (!chapter) continue;
    if (change.kind === "chapter_added") addedChapters.add(chapter.id);
    if (change.kind === "section_added" && chapter.sections.some(item => item.id === change.section_id)) {
      addedSections.add(change.section_id);
    }
  }
  const additions = [
    addedChapters.size ? `${addedChapters.size} 章` : "",
    addedSections.size ? `${addedSections.size} 小节` : "",
  ].filter(Boolean).join("，");
  const text = additions ? `新增 ${additions}`
    : event.book_char_count_delta > 0 ? `新增 ${event.book_char_count_delta} 字`
    : event.changes.some(change => change.kind === "book_updated") ? "书稿已更新"
    : event.changes.length ? "章节内容已更新" : event.summary_text.trim() || "讲述已整理";
  return { id: `growth:${event.event_id}`, kind: "growth", text, eventId: event.event_id };
}

interface HomeNoticeSource extends HomeStoryData {
  notices: HomeNotice[];
  dismissedProgressNoticeId: string;
  shareLikeNotice: ShareLikeNotice | null;
}

/** Keep the current message in place while polling appends newly available messages. */
export function homeNoticeQueue(state: HomeNoticeSource): HomeNotice[] {
  const candidates: HomeNotice[] = [...state.growthNotices];
  const progress = state.progress;
  if (progress && progress.status !== "idle") {
    const action = progress.can_retry ? "retry" : progress.status === "needs_input" ? "review"
      : progress.needs_user_material_count > 0 && progress.status !== "processing" ? "supplement" : "";
    const id = `progress:${JSON.stringify([state.progressNoticeRevision, progress.status, progress.stage, progress.title, action])}`;
    if (id !== state.dismissedProgressNoticeId) {
      const text = progress.status === "ready" ? "书稿已可阅读"
        : progress.status === "partial_ready" ? "已有部分书稿可阅读"
        : [progress.title, progress.detail].filter(Boolean).join("，").replace(/故事结构/g, "章节结构");
      candidates.push({ id, kind: "progress", text, action });
    }
  }
  if (state.shareLikeNotice) {
    candidates.push({ id: `like:${state.shareLikeNotice.latestAt}`, kind: "like",
      text: state.shareLikeNotice.text, people: state.shareLikeNotice });
  }
  if (state.narrationSaved) candidates.push({ id: "saved", kind: "saved", text: "这段讲述已保存。" });
  const pending = new Map(candidates.map(notice => [notice.id, notice]));
  const queue: HomeNotice[] = [];
  for (const notice of state.notices) {
    const current = pending.get(notice.id);
    if (current) { queue.push(current); pending.delete(notice.id); }
  }
  return [...queue, ...pending.values()];
}

export function clearStoryGrowthAnimation(story: StoryViewData): StoryViewData {
  return { ...story, overview: { ...story.overview, growthAnimating: false }, chapters: story.chapters.map(chapter => ({
    ...chapter, growthAnimating: false,
    sections: chapter.sections.map(section => ({ ...section, growthAnimating: false })),
  })) };
}
