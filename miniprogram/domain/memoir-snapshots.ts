import type { MemoirIdentity } from "./memoir";
import type {
  MemoirAutomationEventListResponse,
  MemoirBookChapterDraft,
  MemoirBookOutput,
  MemoirBookPlan,
  MemoirImageAssetListResponse,
  MemoirVoiceTranscriptItem,
} from "./voice-chat";

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
