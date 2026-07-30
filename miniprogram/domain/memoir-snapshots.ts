import type { MemoirIdentity } from "./memoir";
import type { MemoirAutomationEventListResponse } from "./memoir-voice-session";
import type {
  MemoirBookChapterDraft,
  MemoirBookOutput,
  MemoirBookPlan,
  MemoirImageAssetListResponse,
} from "./memoir-book";
import type { MemoirVoiceTranscriptItem } from "./memoir-transcript";

export interface MemoirHomeSnapshot {
  server_revision: number;
  identity: MemoirIdentity;
  latest_transcript?: MemoirVoiceTranscriptItem | null;
}

export interface MemoirBookSnapshot {
  server_revision: number;
  identity: MemoirIdentity;
  plan?: MemoirBookPlan | null;
  drafts_by_chapter_id: Record<string, MemoirBookChapterDraft | null>;
  output?: MemoirBookOutput | null;
  automation_events: MemoirAutomationEventListResponse;
  image_assets: MemoirImageAssetListResponse;
}
