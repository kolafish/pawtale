import type { MemoirDraftProgress, MemoirStoryGrowthEvent, MemoirStorySnapshot } from "../../domain/memoir-snapshots";
import { storyViewData } from "../../components/story-tree/story-view-adapter";
import { emptyStoryViewData, type StoryViewData } from "../../components/story-tree/story-view-data";
import type { ShareLikeNotice } from "../../services/share-likes";

export type HomeNotice =
  | { id: string; kind: "growth"; text: string; eventId: string }
  | { id: string; kind: "progress"; text: string; action: "retry" | "review" | "" }
  | { id: string; kind: "readable"; text: string }
  | { id: string; kind: "like"; text: string; people: ShareLikeNotice };

// v2 replaces the old timestamp-based progress read marker; old values are invalidated.
const NOTICE_READ_STATE_KEY = "pawtale_home_notice_read_state_v2";

export interface HomeNoticeReadState {
  readableSeen: boolean;
  attentionId: string;
}

function noticeReadStateKey(userId: string, projectId: string): string {
  return `${NOTICE_READ_STATE_KEY}:${userId}:${projectId}`;
}

export function readHomeNoticeReadState(userId: string, projectId: string): HomeNoticeReadState {
  try {
    const value: unknown = wx.getStorageSync(noticeReadStateKey(userId, projectId));
    if (value && typeof value === "object" && "readableSeen" in value && "attentionId" in value
      && typeof value.readableSeen === "boolean" && typeof value.attentionId === "string") {
      return { readableSeen: value.readableSeen, attentionId: value.attentionId };
    }
  } catch { /* Keep unread messages available if storage is unavailable. */ }
  return { readableSeen: false, attentionId: "" };
}

export function rememberHomeNoticeReadState(userId: string, projectId: string, state: HomeNoticeReadState): void {
  try { wx.setStorageSync(noticeReadStateKey(userId, projectId), state); }
  catch { /* The message can be shown again if storage is unavailable. */ }
}

export interface HomeStoryData {
  story: StoryViewData;
  storyLoaded: boolean;
  hasStories: boolean;
  storyError: string;
  progress: MemoirDraftProgress | null;
  readableNoticeText: string;
  reviewNoticeScope: string;
  growthNotices: HomeNotice[];
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

  pending(events: MemoirStoryGrowthEvent[]): MemoirStoryGrowthEvent[] {
    // Snapshot history is bounded; keep messages already queued until the user reads them.
    for (const event of events) {
      if (!this.acknowledged.has(event.event_id)
        && (event.book_char_count_delta > 0
          || event.changes.some(change => change.kind === "chapter_added" || change.kind === "section_added"))) {
        this.queued.set(event.event_id, event);
      }
    }
    return [...this.queued.values()];
  }

  reset(): void { this.scope = ""; this.seen.clear(); this.acknowledged.clear(); this.queued.clear(); }
}

export function emptyHomeStoryData(): HomeStoryData {
  return {
    story: emptyStoryViewData(), storyLoaded: false, hasStories: false, storyError: "", progress: null, readableNoticeText: "", reviewNoticeScope: "",
    growthNotices: [],
    dismissingGrowth: false, retryingDraft: false,
  };
}

export function homeStorySnapshotData(
  snapshot: MemoirStorySnapshot,
  freshEventIds: Set<string> = new Set(),
  pendingGrowthEvents: MemoirStoryGrowthEvent[] = snapshot.growth_events,
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
  const includedChapters = snapshot.chapters.filter(chapter => chapter.include_in_book);
  const readable = includedChapters.filter(chapter => chapter.readable).length;
  const hasReadableDraft = snapshot.book_readable || readable > 0;
  const fullyReadable = snapshot.progress?.status === "ready"
    || (!snapshot.progress && includedChapters.length > 0 && readable === includedChapters.length);
  return {
    story, storyLoaded: true, hasStories: snapshot.chapters.length > 0, storyError: "", progress: snapshot.progress,
    reviewNoticeScope: JSON.stringify([snapshot.book_manual_edit_preserved, snapshot.review_issue_signature,
      snapshot.chapters.filter(chapter => chapter.candidate_job_id).sort((a, b) => a.id.localeCompare(b.id))
        .map(chapter => [chapter.id, chapter.candidate_job_id])]),
    readableNoticeText: hasReadableDraft ? (fullyReadable ? "书稿已可阅读" : "已有部分书稿可阅读") : "",
    growthNotices: [...pendingGrowthEvents]
      .sort((a, b) => a.created_at.localeCompare(b.created_at))
      .flatMap(event => {
        const notice = growthNotice(event, story);
        return notice ? [notice] : [];
      }),
  };
}

function growthNotice(event: MemoirStoryGrowthEvent, story: StoryViewData): HomeNotice | null {
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
    event.book_char_count_delta > 0 ? `${event.book_char_count_delta} 字` : "",
  ].filter(Boolean).join("，");
  return additions
    ? { id: `growth:${event.event_id}`, kind: "growth", text: `新增 ${additions}`, eventId: event.event_id }
    : null;
}

interface HomeNoticeSource extends HomeStoryData {
  notices: HomeNotice[];
  noticeReadState: HomeNoticeReadState;
  shareLikeNotice: ShareLikeNotice | null;
}

/** Keep the current message in place while polling appends newly available messages. */
export function homeNoticeQueue(state: HomeNoticeSource): HomeNotice[] {
  const candidates: HomeNotice[] = [...state.growthNotices];
  const progress = state.progress;
  if (progress && (progress.status === "failed" || progress.status === "needs_input")) {
    const action = progress.status === "needs_input" ? "review" : progress.can_retry ? "retry" : "";
    const text = [progress.title, progress.detail].filter(Boolean).join("，");
    const id = `attention:${JSON.stringify([progress.status, text, action,
      progress.status === "needs_input" ? state.reviewNoticeScope : ""])}`;
    if (id !== state.noticeReadState.attentionId) candidates.push({ id, kind: "progress", text, action });
  }
  if (state.readableNoticeText && !state.noticeReadState.readableSeen) {
    candidates.push({ id: "readable", kind: "readable", text: state.readableNoticeText });
  }
  if (state.shareLikeNotice) {
    candidates.push({ id: `like:${state.shareLikeNotice.latestAt}`, kind: "like",
      text: state.shareLikeNotice.text, people: state.shareLikeNotice });
  }
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
