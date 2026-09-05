import type { MemoirAutomationEvent } from "../domain/memoir-voice-session";
import { listMemoirAutomationEvents } from "./memoir-voice-session-api";
import { PollingTimer } from "./polling-timer";

const STORY_TAB_INDEX = 1;

function stringList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string" && Boolean(item))
    : [];
}

export function storyProgressEvents(
  events: MemoirAutomationEvent[] = [],
): MemoirAutomationEvent[] {
  return events.filter((event) => (
    event.status === "completed"
    && !event.metadata_json?.acknowledged_at
    && Boolean(event.summary_text.trim())
  )).sort((a, b) => (b.created_at || "").localeCompare(a.created_at || ""));
}

export function storyProgressTitle(event: MemoirAutomationEvent): string {
  return {
    story_structure_updated: "故事有了新进展",
    chapter_draft_generated: "章节正文已更新",
    book_output_generated: "可读书稿已更新",
    book_editorial_copy_generated: "成书文案已更新",
  }[event.event_type];
}

export function storyGrowthTargets(event: MemoirAutomationEvent | null): {
  chapterIds: string[];
  sectionIds: string[];
} {
  return {
    chapterIds: event?.chapter_id ? [event.chapter_id] : stringList(event?.metadata_json?.new_chapter_ids),
    sectionIds: stringList(event?.metadata_json?.new_section_ids),
  };
}

export function syncStoryTabRedDot(events: MemoirAutomationEvent[] = []): void {
  if (typeof wx === "undefined") {
    return;
  }
  const visible = storyProgressEvents(events).length > 0;
  const tabBar = wx as typeof wx & {
    showTabBarRedDot?: (options: { index: number }) => void;
    hideTabBarRedDot?: (options: { index: number }) => void;
  };
  if (visible) {
    tabBar.showTabBarRedDot?.({ index: STORY_TAB_INDEX });
  } else {
    tabBar.hideTabBarRedDot?.({ index: STORY_TAB_INDEX });
  }
}

export class StoryProgressWatcher {
  private token = 0;
  projectId = "";

  constructor(private readonly timer = new PollingTimer(5000)) {}

  stop(): void {
    this.token += 1;
    this.projectId = "";
    this.timer.cancel();
  }

  start(projectId: string, onEvents: (events: MemoirAutomationEvent[]) => void): void {
    this.stop();
    if (!projectId) return;
    this.projectId = projectId;
    const token = this.token;
    const refresh = async () => {
      try {
        const response = await listMemoirAutomationEvents(projectId, 8);
        if (token === this.token) onEvents(storyProgressEvents(response.items));
      } catch (error) {
        if (token === this.token) console.warn("refresh story progress notice failed", error);
      }
      if (token === this.token) this.timer.schedule(() => { void refresh(); });
    };
    void refresh();
  }
}
