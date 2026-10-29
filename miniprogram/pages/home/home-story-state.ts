import type { MemoirDraftProgress, MemoirStoryGrowthEvent, MemoirStorySnapshot } from "../../domain/memoir-snapshots";
import { storyViewData } from "../../components/story-tree/story-view-adapter";
import { emptyStoryViewData, type StoryViewData } from "../../components/story-tree/story-view-data";
import type { ShareLikeNotice } from "../../services/share-likes";

export type HomeNotice =
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
  growthResult: HomeGrowthResult | null;
  growthResultReadingId: string;
  retryingDraft: boolean;
}

export interface HomeGrowthResult {
  id: string;
  eventIds: string[];
  titles: { id: string; title: string; label: string }[];
  moreCount: number;
  wordDelta: number;
  celebrate: boolean;
}

/** Receipts are scoped to the author and memoir, and written only after visible playback. */
export class HomeGrowthTracker {
  private scope = "";
  private seen = new Set<string>();
  private acknowledged = new Set<string>();
  private queued = new Map<string, MemoirStoryGrowthEvent>();
  private receipts = new Set<string>();
  private storageKey = "";
  private syncing = new Set<string>();
  private generation = 0;

  observe(userId: string, projectId: string, events: MemoirStoryGrowthEvent[]): Set<string> {
    const scope = `${userId}/${projectId}`;
    const firstSnapshot = this.scope !== scope;
    if (firstSnapshot) {
      this.reset(); this.scope = scope;
      this.storageKey = `pawtale_home_growth_played_v1:${userId}:${projectId}`;
      try {
        const stored: unknown = wx.getStorageSync(this.storageKey);
        if (Array.isArray(stored)) this.receipts = new Set(stored.filter((id): id is string => typeof id === "string").slice(-128));
      } catch { /* Server acknowledgements remain the durable source when local storage is unavailable. */ }
    }
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

  played(eventIds: string[]): void {
    eventIds.forEach(id => { this.receipts.add(id); this.queued.delete(id); });
    try { wx.setStorageSync(this.storageKey, [...this.receipts].slice(-128)); }
    catch { /* Keep receipts in memory for this visit. */ }
  }

  async syncAcknowledgements(events: MemoirStoryGrowthEvent[], acknowledge: (id: string) => Promise<unknown>): Promise<void> {
    const generation = this.generation;
    const ids = [...new Set(events.map(event => event.event_id))]
      .filter(id => this.receipts.has(id) && !this.acknowledged.has(id) && !this.syncing.has(id));
    await Promise.all(ids.map(async id => {
      this.syncing.add(id);
      try {
        await acknowledge(id);
        if (this.generation === generation) this.acknowledge(id);
      } catch (error) {
        // The next snapshot retries the receipt; a network failure must not replay a seen result.
        console.warn("sync home growth receipt failed", error);
      } finally { if (this.generation === generation) this.syncing.delete(id); }
    }));
  }

  pending(events: MemoirStoryGrowthEvent[]): MemoirStoryGrowthEvent[] {
    // Snapshot history is bounded; keep deltas, including reductions, until the result is read.
    for (const event of events) {
      if (!this.acknowledged.has(event.event_id) && !this.receipts.has(event.event_id)
        && (event.book_char_count_delta !== 0
          || event.changes.some(change => change.kind === "chapter_added" || change.kind === "section_added"))) {
        this.queued.set(event.event_id, event);
      }
    }
    return [...this.queued.values()];
  }

  reset(): void { this.generation += 1; this.scope = ""; this.seen.clear(); this.acknowledged.clear(); this.queued.clear(); this.receipts.clear(); this.syncing.clear(); }
}

export function emptyHomeStoryData(): HomeStoryData {
  return {
    story: emptyStoryViewData(), storyLoaded: false, hasStories: false, storyError: "", progress: null, readableNoticeText: "", reviewNoticeScope: "",
    growthResult: null, growthResultReadingId: "", retryingDraft: false,
  };
}

export function homeStorySnapshotData(
  snapshot: MemoirStorySnapshot,
  freshEventIds: Set<string> = new Set(),
): Partial<HomeStoryData> & Pick<HomeStoryData, "story"> {
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
  };
}

export function homeGrowthResult(events: MemoirStoryGrowthEvent[], story: StoryViewData): HomeGrowthResult | null {
  const unique = [...new Map(events.map(event => [event.event_id, event])).values()]
    .sort((a, b) => a.created_at.localeCompare(b.created_at) || a.event_id.localeCompare(b.event_id));
  const titles = new Map<string, { id: string; title: string; label: string; added: boolean; chapterId: string; section: boolean }>();
  const relevant: MemoirStoryGrowthEvent[] = [];
  for (const event of unique) {
    let valid = false;
    for (const change of event.changes) {
      if (change.kind === "book_updated") { valid = true; continue; }
      const chapter = story.chapters.find(item => item.id === change.chapter_id);
      if (!chapter) continue;
      const section = "section_id" in change ? chapter.sections.find(item => item.id === change.section_id) : null;
      if ("section_id" in change && !section) continue;
      valid = true;
      const target = section || chapter;
      const id = `${section ? "section" : "chapter"}:${target.id}`;
      const added = change.kind === "chapter_added" || change.kind === "section_added" || Boolean(titles.get(id)?.added);
      titles.set(id, { id, title: target.title, label: added ? (section ? "新增小节" : "新增章节") : "补充",
        added, chapterId: chapter.id, section: Boolean(section) });
    }
    if (valid) relevant.push(event);
  }
  // Signed chapter deltas telescope across revisions; counting only positives overstates growth.
  const wordDelta = Math.max(0, relevant.reduce((sum, event) => sum + event.book_char_count_delta, 0));
  const allTitles = [...titles.values()];
  const added = allTitles.some(item => item.added);
  if (!added && wordDelta <= 0) return null;
  const visibleTitles = allTitles.filter(item => item.section || item.added || !allTitles.some(other => other.section && other.chapterId === item.chapterId))
    .sort((a, b) => Number(b.added) - Number(a.added));
  const eventIds = relevant.map(event => event.event_id);
  return { id: JSON.stringify(eventIds), eventIds,
    titles: visibleTitles.slice(0, 2).map(({ id, title, label }) => ({ id, title, label })),
    moreCount: Math.max(0, visibleTitles.length - 2), wordDelta, celebrate: added || wordDelta >= 50 };
}

interface HomeNoticeSource extends HomeStoryData {
  notices: HomeNotice[];
  noticeReadState: HomeNoticeReadState;
  shareLikeNotice: ShareLikeNotice | null;
}

/** Keep the current message in place while polling appends newly available messages. */
export function homeNoticeQueue(state: HomeNoticeSource): HomeNotice[] {
  const candidates: HomeNotice[] = [];
  const progress = state.progress;
  if (progress && (progress.status === "failed" || progress.status === "needs_input")) {
    const action = progress.status === "needs_input" ? "review" : progress.can_retry ? "retry" : "";
    const text = [progress.title, progress.detail].filter(Boolean).join("，");
    const id = `attention:${JSON.stringify([progress.status, text, action,
      progress.status === "needs_input" ? state.reviewNoticeScope : ""])}`;
    if (id !== state.noticeReadState.attentionId) candidates.push({ id, kind: "progress", text, action });
  }
  if (state.readableNoticeText && !state.noticeReadState.readableSeen && !state.growthResult) {
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
