import type {
  MemoirAnswerSegment,
  MemoirAnswerSegmentStatus,
  MemoirAnswerSession,
  MemoirAnswerSessionStatus,
} from "../domain/memoir";

const STORE_KEY = "pawtale_memoir_answer_session_drafts";
const ANSWER_SESSION_DRAFT_VERSION = 1;
const ANSWER_SESSION_DRAFT_MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000;
const ANSWER_SESSION_DRAFT_MAX_ITEMS = 20;

export type MemoirAnswerSessionDraftStatus = MemoirAnswerSessionStatus | "local";
export type MemoirAnswerSessionDraftSegmentStatus =
  | MemoirAnswerSegmentStatus
  | "local"
  | "uploading"
  | "transcribing"
  | "submitted";

export interface MemoirAnswerSessionDraftSegment {
  local_id: string;
  answer_segment_id?: string;
  audio_file_id?: string | null;
  temp_file_path?: string;
  sort_order: number;
  status: MemoirAnswerSessionDraftSegmentStatus;
  duration_seconds?: number | null;
  transcript_raw: string;
  transcript_edited: string;
  error_message?: string;
  created_at: string;
  updated_at: string;
}

export interface MemoirAnswerSessionDraft {
  project_id: string;
  session_id?: string;
  question_id: string;
  answer_session_id?: string;
  answer_id?: string | null;
  status: MemoirAnswerSessionDraftStatus;
  segments: MemoirAnswerSessionDraftSegment[];
  merged_text: string;
  pending_upload_count: number;
  created_at: string;
  updated_at: string;
  synced_at?: string;
}

interface TimedDraft {
  updated_at: string;
}

interface MemoirAnswerSessionDraftStore {
  version: number;
  drafts: Record<string, MemoirAnswerSessionDraft>;
}

function emptyStore(): MemoirAnswerSessionDraftStore {
  return {
    version: ANSWER_SESSION_DRAFT_VERSION,
    drafts: {},
  };
}

function draftKey(projectId: string, questionId: string): string {
  return `${projectId}:${questionId}`;
}

function readStore(): MemoirAnswerSessionDraftStore {
  const store = wx.getStorageSync<MemoirAnswerSessionDraftStore>(STORE_KEY);
  return store?.version === ANSWER_SESSION_DRAFT_VERSION ? store : emptyStore();
}

function draftAgeMs(updatedAt: string): number {
  const timestamp = new Date(updatedAt).getTime();
  return Number.isNaN(timestamp) ? Number.POSITIVE_INFINITY : Date.now() - timestamp;
}

function expiredDraft(draft?: TimedDraft): boolean {
  return Boolean(draft && draftAgeMs(draft.updated_at) > ANSWER_SESSION_DRAFT_MAX_AGE_MS);
}

function pruneDrafts(
  drafts: Record<string, MemoirAnswerSessionDraft>,
): Record<string, MemoirAnswerSessionDraft> {
  return Object.fromEntries(
    Object.entries(drafts)
      .filter(([, draft]) => draftAgeMs(draft.updated_at) <= ANSWER_SESSION_DRAFT_MAX_AGE_MS)
      .sort(([, left], [, right]) => String(right.updated_at).localeCompare(String(left.updated_at)))
      .slice(0, ANSWER_SESSION_DRAFT_MAX_ITEMS),
  );
}

function persistStore(store: MemoirAnswerSessionDraftStore): void {
  try {
    wx.setStorageSync(STORE_KEY, {
      version: ANSWER_SESSION_DRAFT_VERSION,
      drafts: pruneDrafts(store.drafts),
    });
  } catch {
    // Drafts are best-effort; losing local progress should not block server calls.
  }
}

function segmentText(segment: Pick<MemoirAnswerSessionDraftSegment, "transcript_raw" | "transcript_edited">): string {
  return (segment.transcript_edited || segment.transcript_raw || "").trim();
}

function mergedText(segments: MemoirAnswerSessionDraftSegment[]): string {
  return [...segments]
    .sort((left, right) => left.sort_order - right.sort_order)
    .map(segmentText)
    .filter(Boolean)
    .join("\n\n");
}

function pendingUploadCount(segments: MemoirAnswerSessionDraftSegment[]): number {
  return segments.filter((segment) => (
    segment.status === "local"
    || segment.status === "uploading"
    || Boolean(segment.temp_file_path && !segment.audio_file_id)
  )).length;
}

function normalizeDraft(
  draft: Omit<MemoirAnswerSessionDraft, "merged_text" | "pending_upload_count" | "updated_at"> & { updated_at?: string },
): MemoirAnswerSessionDraft {
  const now = new Date().toISOString();
  const segments = [...draft.segments].sort((left, right) => left.sort_order - right.sort_order);
  return {
    ...draft,
    segments,
    merged_text: mergedText(segments),
    pending_upload_count: pendingUploadCount(segments),
    created_at: draft.created_at || now,
    updated_at: now,
  };
}

function localSegmentId(segment: Pick<MemoirAnswerSegment, "id">): string {
  return `remote:${segment.id}`;
}

function draftSegmentFromRemote(segment: MemoirAnswerSegment): MemoirAnswerSessionDraftSegment {
  return {
    local_id: localSegmentId(segment),
    answer_segment_id: segment.id,
    audio_file_id: segment.audio_file_id,
    sort_order: segment.sort_order,
    status: segment.status,
    duration_seconds: segment.duration_seconds,
    transcript_raw: segment.transcript_raw || "",
    transcript_edited: segment.transcript_edited || "",
    error_message: segment.error_message || "",
    created_at: segment.created_at,
    updated_at: segment.updated_at,
  };
}

export function clearMemoirAnswerSessionDrafts(): void {
  try {
    wx.removeStorageSync(STORE_KEY);
  } catch {
    // Local drafts are best effort; failing to clear them should not block identity reset.
  }
}

export function readMemoirAnswerSessionDraft(
  projectId: string,
  questionId: string,
): MemoirAnswerSessionDraft | undefined {
  const store = readStore();
  const key = draftKey(projectId, questionId);
  const draft = store.drafts[key];
  if (expiredDraft(draft)) {
    delete store.drafts[key];
    persistStore(store);
    return undefined;
  }
  return draft;
}

export function writeMemoirAnswerSessionDraft(
  input: Omit<MemoirAnswerSessionDraft, "merged_text" | "pending_upload_count" | "updated_at"> & { updated_at?: string },
): MemoirAnswerSessionDraft {
  const store = readStore();
  const draft = normalizeDraft(input);
  store.drafts[draftKey(draft.project_id, draft.question_id)] = draft;
  persistStore(store);
  return draft;
}

export function updateMemoirAnswerSessionDraft(
  projectId: string,
  questionId: string,
  updater: (draft: MemoirAnswerSessionDraft | undefined) => MemoirAnswerSessionDraft | undefined,
): MemoirAnswerSessionDraft | undefined {
  const store = readStore();
  const key = draftKey(projectId, questionId);
  const existing = expiredDraft(store.drafts[key]) ? undefined : store.drafts[key];
  const next = updater(existing);
  if (!next) {
    delete store.drafts[key];
    persistStore(store);
    return undefined;
  }
  const draft = normalizeDraft(next);
  delete store.drafts[key];
  store.drafts[draftKey(draft.project_id, draft.question_id)] = draft;
  persistStore(store);
  return draft;
}

export function writeMemoirAnswerSessionDraftFromRemote(
  answerSession: MemoirAnswerSession,
): MemoirAnswerSessionDraft {
  const now = new Date().toISOString();
  return writeMemoirAnswerSessionDraft({
    project_id: answerSession.project_id,
    session_id: answerSession.session_id,
    question_id: answerSession.question_id,
    answer_session_id: answerSession.id,
    answer_id: answerSession.answer_id,
    status: answerSession.status,
    segments: answerSession.segments.map(draftSegmentFromRemote),
    created_at: answerSession.created_at || now,
    synced_at: now,
  });
}

export function clearMemoirAnswerSessionDraft(projectId: string, questionId: string): void {
  const store = readStore();
  delete store.drafts[draftKey(projectId, questionId)];
  persistStore(store);
}
