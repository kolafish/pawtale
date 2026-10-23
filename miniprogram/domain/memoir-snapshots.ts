import type { MemoirIdentity } from "./memoir";
import type { MemoirAutomationEventListResponse } from "./memoir-voice-session";
import type {
  MemoirBookChapterDraft,
  MemoirBookOutput,
  MemoirBookPlan,
  MemoirImageAssetListResponse,
} from "./memoir-book";
import type { MemoirSharedVideo, MemoirVoiceTranscriptItem } from "./memoir-transcript";

export interface MemoirHomeSnapshot {
  server_revision: number;
  identity: MemoirIdentity;
  latest_transcript?: MemoirVoiceTranscriptItem | null;
}

export interface MemoirShareVideoCount {
  utterance_id: string;
  count: number;
  job_ids: string[];
  current_job_ids?: string[];
  job_titles?: Record<string, string>;
}

export interface MemoirBookSnapshot {
  server_revision: number;
  identity: MemoirIdentity;
  plan?: MemoirBookPlan | null;
  drafts_by_chapter_id: Record<string, MemoirBookChapterDraft | null>;
  output?: MemoirBookOutput | null;
  progress?: MemoirDraftProgress | null;
  blocking_issue_count: number;
  automation_events: MemoirAutomationEventListResponse;
  image_assets: MemoirImageAssetListResponse;
  share_video_counts: MemoirShareVideoCount[];
}

export interface MemoirDraftProgress {
  project_id: string;
  status: "idle" | "processing" | "ready" | "partial_ready" | "needs_input" | "no_change" | "failed";
  stage: string;
  title: string;
  detail: string;
  can_retry: boolean;
  readable_chapter_count: number;
  total_chapter_count: number;
  material_count: number;
  pending_material_count: number;
  needs_user_material_count: number;
  review_status: string;
  review_error: string;
}

export interface MemoirStoryIssueCounts {
  review_count: number;
  supplement_count: number;
}

export interface MemoirStoryChapter {
  issues: MemoirStoryIssueCounts;
  id: string;
  title: string;
  include_in_book: boolean;
  material_count: number;
  image_count: number;
  video_count: number;
  char_count: number;
  readable: boolean;
  candidate_job_id: string | null;
  state: "settled" | "attention" | "draft" | "muted";
  state_text: string;
  sections: Array<{ id: string; title: string; include_in_book: boolean; material_count: number; image_count: number; video_count: number }>;
}

export type MemoirStoryChange =
  | { kind: "chapter_added" | "chapter_updated"; chapter_id: string }
  | { kind: "section_added" | "section_updated"; chapter_id: string; section_id: string }
  | { kind: "book_updated" };

export interface MemoirStoryGrowthEvent {
  event_id: string;
  summary_text: string;
  created_at: string;
  book_char_count_delta: number;
  changes: MemoirStoryChange[];
}

export interface MemoirStorySnapshot extends MemoirHomeSnapshot {
  book_issues: MemoirStoryIssueCounts;
  book_manual_edit_preserved: boolean;
  review_issue_signature: string;
  book_chapter_count: number;
  progress: MemoirDraftProgress | null;
  chapters: MemoirStoryChapter[];
  book_readable: boolean;
  book_char_count: number;
  updated_at: string;
  unassigned_image_count: number;
  unassigned_video_count: number;
  automation_events: MemoirAutomationEventListResponse;
  growth_events: MemoirStoryGrowthEvent[];
}

export interface MemoirDemoVideo extends MemoirSharedVideo {
  share_code: string;
}

/** A sample memoir visitors can browse before logging in. */
export interface MemoirDemoStory {
  title: string;
  chapters: MemoirStoryChapter[];
  book_chapter_count: number;
  book_char_count: number;
  videos: MemoirDemoVideo[];
}
