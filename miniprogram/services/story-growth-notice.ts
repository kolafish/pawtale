import type { MemoirAutomationEvent } from "../domain/memoir-voice-session";
import { listMemoirAutomationEvents } from "./memoir-voice-session-api";
import { PollingTimer } from "./polling-timer";

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
        const response = await listMemoirAutomationEvents(projectId, 8, { completedOnly: true });
        if (token === this.token) onEvents(storyProgressEvents(response.items));
      } catch (error) {
        if (token === this.token) console.warn("refresh story progress notice failed", error);
      }
      if (token === this.token) this.timer.schedule(() => { void refresh(); });
    };
    void refresh();
  }
}
