import type { MemoirDraftProgress, MemoirStoryGrowthEvent, MemoirStorySnapshot } from "../../domain/memoir-snapshots";
import { storyViewData } from "../../components/story-tree/story-view-adapter";
import { emptyStoryViewData, highlightStoryGrowth, type StoryViewData } from "../../components/story-tree/story-view-data";

export interface HomeStoryData {
  story: StoryViewData;
  storyLoaded: boolean;
  storyError: string;
  progress: MemoirDraftProgress | null;
  bookReadable: boolean;
  growthEvents: MemoirStoryGrowthEvent[];
  growthNoticeTitle: string;
  growthNoticeText: string;
  dismissingGrowth: boolean;
  retryingDraft: boolean;
}

export function emptyHomeStoryData(): HomeStoryData {
  return {
    story: emptyStoryViewData(), storyLoaded: false, storyError: "", progress: null,
    bookReadable: false, growthEvents: [], growthNoticeTitle: "", growthNoticeText: "",
    dismissingGrowth: false, retryingDraft: false,
  };
}

export function homeStorySnapshotData(snapshot: MemoirStorySnapshot, previous: StoryViewData): Partial<HomeStoryData> {
  const story = storyViewData(snapshot);
  const expanded = new Map(previous.chapters.map((chapter) => [chapter.id, chapter.expanded]));
  const changes = snapshot.growth_events.flatMap((event) => event.changes);
  story.chapters = highlightStoryGrowth(
    story.chapters.map((chapter) => ({ ...chapter, expanded: expanded.get(chapter.id) ?? chapter.expanded })),
    changes.flatMap((change) => change.kind === "chapter_added" || change.kind === "chapter_updated" ? [change.chapter_id] : []),
    changes.flatMap((change) => "section_id" in change ? [change.section_id] : []),
  );
  return {
    story, storyLoaded: true, storyError: "", progress: snapshot.progress,
    bookReadable: snapshot.book_readable, growthEvents: snapshot.growth_events,
    growthNoticeTitle: "故事有了新进展",
    growthNoticeText: snapshot.growth_events.map((event) => event.summary_text).join("\n"),
  };
}
