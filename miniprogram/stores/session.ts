import type { MemoirAnswer, MemoirDraft, MemoirInterviewState, Project } from "../domain/memoir";

const STORE_KEY = "pawtale_memoir_session";

export interface MemoirSessionStore {
  project?: Project;
  interview?: MemoirInterviewState;
  answer?: MemoirAnswer;
  draft?: MemoirDraft;
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
  });
}
