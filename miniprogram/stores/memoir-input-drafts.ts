const STORE_KEY = "pawtale_memoir_input_drafts";
const INPUT_DRAFT_VERSION = 1;
const INPUT_DRAFT_MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000;
const INPUT_DRAFT_MAX_ITEMS = 30;

interface TimedDraft {
  updated_at: string;
}

export interface MemoirQuestionInputDraft extends TimedDraft {
  question_id: string;
  answer_text: string;
  audio_file_id?: string;
  audio_duration_seconds?: number | null;
  audio_summary?: string;
}

export interface MemoirQaDetailInputDraft extends TimedDraft {
  answer_id: string;
  question_id?: string;
  answer_text: string;
  new_audio_file_id?: string;
  new_audio_duration_seconds?: number | null;
  new_audio_summary?: string;
}

export interface MemoirEditorInputDraft extends TimedDraft {
  answer_id: string;
  draft_id: string;
  transcript_text: string;
  draft_text: string;
}

interface MemoirInputDraftStore {
  version: number;
  questions: Record<string, MemoirQuestionInputDraft>;
  qa_details: Record<string, MemoirQaDetailInputDraft>;
  editors: Record<string, MemoirEditorInputDraft>;
}

function emptyStore(): MemoirInputDraftStore {
  return {
    version: INPUT_DRAFT_VERSION,
    questions: {},
    qa_details: {},
    editors: {},
  };
}

function readStore(): MemoirInputDraftStore {
  const store = wx.getStorageSync<MemoirInputDraftStore>(STORE_KEY);
  return store?.version === INPUT_DRAFT_VERSION ? store : emptyStore();
}

function draftAgeMs(updatedAt: string): number {
  const timestamp = new Date(updatedAt).getTime();
  return Number.isNaN(timestamp) ? Number.POSITIVE_INFINITY : Date.now() - timestamp;
}

function expiredDraft(draft?: TimedDraft): boolean {
  return Boolean(draft && draftAgeMs(draft.updated_at) > INPUT_DRAFT_MAX_AGE_MS);
}

function pruneDrafts<T extends TimedDraft>(drafts: Record<string, T>): Record<string, T> {
  return Object.fromEntries(
    Object.entries(drafts)
      .filter(([, draft]) => draftAgeMs(draft.updated_at) <= INPUT_DRAFT_MAX_AGE_MS)
      .sort(([, left], [, right]) => String(right.updated_at).localeCompare(String(left.updated_at)))
      .slice(0, INPUT_DRAFT_MAX_ITEMS),
  );
}

function persistStore(store: MemoirInputDraftStore): void {
  try {
    wx.setStorageSync(STORE_KEY, {
      version: INPUT_DRAFT_VERSION,
      questions: pruneDrafts(store.questions),
      qa_details: pruneDrafts(store.qa_details),
      editors: pruneDrafts(store.editors),
    });
  } catch {
    // Local drafts are best effort; server writes must not fail because storage is full.
  }
}

export function clearMemoirInputDrafts(): void {
  try {
    wx.removeStorageSync(STORE_KEY);
  } catch {
    // Local drafts are best effort; failing to clear them should not block session refresh.
  }
}

function editorKey(answerId: string, draftId: string): string {
  return `${answerId}:${draftId}`;
}

export function readMemoirQuestionInputDraft(questionId: string): MemoirQuestionInputDraft | undefined {
  const store = readStore();
  const draft = store.questions[questionId];
  if (expiredDraft(draft)) {
    delete store.questions[questionId];
    persistStore(store);
    return undefined;
  }
  return draft;
}

export function writeMemoirQuestionInputDraft(input: Omit<MemoirQuestionInputDraft, "updated_at">): void {
  const store = readStore();
  if (!input.answer_text.trim() && !input.audio_file_id && !input.audio_summary) {
    delete store.questions[input.question_id];
    persistStore(store);
    return;
  }
  store.questions[input.question_id] = {
    ...input,
    updated_at: new Date().toISOString(),
  };
  persistStore(store);
}

export function clearMemoirQuestionInputDraft(questionId: string): void {
  const store = readStore();
  delete store.questions[questionId];
  persistStore(store);
}

export function readMemoirQaDetailInputDraft(answerId: string): MemoirQaDetailInputDraft | undefined {
  const store = readStore();
  const draft = store.qa_details[answerId];
  if (expiredDraft(draft)) {
    delete store.qa_details[answerId];
    persistStore(store);
    return undefined;
  }
  return draft;
}

export function writeMemoirQaDetailInputDraft(input: Omit<MemoirQaDetailInputDraft, "updated_at">): void {
  const store = readStore();
  store.qa_details[input.answer_id] = {
    ...input,
    updated_at: new Date().toISOString(),
  };
  persistStore(store);
}

export function clearMemoirQaDetailInputDraft(answerId: string): void {
  const store = readStore();
  delete store.qa_details[answerId];
  persistStore(store);
}

export function readMemoirEditorInputDraft(answerId: string, draftId: string): MemoirEditorInputDraft | undefined {
  const store = readStore();
  const key = editorKey(answerId, draftId);
  const draft = store.editors[key];
  if (expiredDraft(draft)) {
    delete store.editors[key];
    persistStore(store);
    return undefined;
  }
  return draft;
}

export function writeMemoirEditorInputDraft(input: Omit<MemoirEditorInputDraft, "updated_at">): void {
  const store = readStore();
  store.editors[editorKey(input.answer_id, input.draft_id)] = {
    ...input,
    updated_at: new Date().toISOString(),
  };
  persistStore(store);
}

export function clearMemoirEditorInputDraft(answerId: string, draftId: string): void {
  const store = readStore();
  delete store.editors[editorKey(answerId, draftId)];
  persistStore(store);
}
