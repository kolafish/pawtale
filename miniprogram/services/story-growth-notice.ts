import type { MemoirAutomationEvent } from "../domain/memoir-voice-session";

const STORY_TAB_INDEX = 1;

function stringList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string" && Boolean(item))
    : [];
}

export function storyGrowthEvent(
  events: MemoirAutomationEvent[] = [],
  voiceSessionId = "",
): MemoirAutomationEvent | null {
  return events.find((event) => (
    event.event_type === "story_structure_updated"
    && event.status === "completed"
    && !event.metadata_json?.acknowledged_at
    && (
      !voiceSessionId
      || event.metadata_json?.voice_session_id === voiceSessionId
    )
  )) || null;
}

export function storyGrowthTargets(event: MemoirAutomationEvent | null): {
  chapterIds: string[];
  sectionIds: string[];
} {
  return {
    chapterIds: stringList(event?.metadata_json?.new_chapter_ids),
    sectionIds: stringList(event?.metadata_json?.new_section_ids),
  };
}

export function syncStoryTabRedDot(events: MemoirAutomationEvent[] = []): void {
  if (typeof wx === "undefined") {
    return;
  }
  const visible = Boolean(storyGrowthEvent(events));
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
