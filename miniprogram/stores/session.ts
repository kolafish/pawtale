import type {
  MemoirAnswer,
  MemoirDraft,
  MemoirInterviewState,
  MemoirQuestion,
  MemoirStoryCard,
  MemoirSummary,
  Project,
} from "../domain/memoir";

const STORE_KEY = "pawtale_memoir_session";

export interface MemoirSessionStore {
  project?: Project;
  interview?: MemoirInterviewState;
  question?: MemoirQuestion;
  answer?: MemoirAnswer;
  draft?: MemoirDraft;
  summary?: MemoirSummary;
  stories?: MemoirStoryCard[];
  cached_at?: string;
}

export function readMemoirStore(): MemoirSessionStore {
  return wx.getStorageSync<MemoirSessionStore>(STORE_KEY) || {};
}

export function writeMemoirStore(patch: MemoirSessionStore): MemoirSessionStore {
  const next = {
    ...readMemoirStore(),
    ...patch,
  };
  wx.setStorageSync(STORE_KEY, next);
  return next;
}

export function clearMemoirDraft(): void {
  const current = readMemoirStore();
  wx.setStorageSync(STORE_KEY, {
    project: current.project,
    interview: current.interview,
    summary: current.summary,
    stories: current.stories,
    cached_at: current.cached_at,
  });
}
