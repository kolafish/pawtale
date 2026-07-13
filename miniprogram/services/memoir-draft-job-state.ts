export type MemoirDraftJobMode = "generate" | "revise";

export interface MemoirDraftJobCursor {
  projectId: string;
  chapterId: string;
  jobId: string;
  mode: MemoirDraftJobMode;
  blockId: string;
}

interface MemoirDraftJobStore {
  version: 1;
  cursors: Record<string, MemoirDraftJobCursor>;
}

const STORAGE_KEY = "pawtale_memoir_draft_job_state";


function storageApi(): Pick<WechatMiniprogram.Wx, "getStorageSync" | "setStorageSync" | "removeStorageSync"> | null {
  if (
    typeof wx === "undefined"
    || typeof wx.getStorageSync !== "function"
    || typeof wx.setStorageSync !== "function"
    || typeof wx.removeStorageSync !== "function"
  ) {
    return null;
  }
  return wx;
}

function cursorKey(projectId: string, chapterId: string): string {
  return `${projectId}:${chapterId}`;
}

function emptyStore(): MemoirDraftJobStore {
  return { version: 1, cursors: {} };
}

function validCursor(value: unknown): value is MemoirDraftJobCursor {
  if (!value || typeof value !== "object") {
    return false;
  }
  const cursor = value as Partial<MemoirDraftJobCursor>;
  return Boolean(
    cursor.projectId
    && cursor.chapterId
    && cursor.jobId
    && (cursor.mode === "generate" || cursor.mode === "revise")
    && typeof cursor.blockId === "string",
  );
}

function readStore(): MemoirDraftJobStore {
  const storage = storageApi();
  if (!storage) {
    return emptyStore();
  }
  try {
    const value = storage.getStorageSync(STORAGE_KEY) as Partial<MemoirDraftJobStore> | null;
    if (!value || value.version !== 1 || !value.cursors || typeof value.cursors !== "object") {
      return emptyStore();
    }
    const cursors = Object.fromEntries(
      Object.entries(value.cursors).filter(([, cursor]) => validCursor(cursor)),
    );
    return { version: 1, cursors };
  } catch (error) {
    console.warn("read memoir draft job state failed", error);
    return emptyStore();
  }
}

function writeStore(store: MemoirDraftJobStore): void {
  const storage = storageApi();
  if (!storage) {
    return;
  }
  try {
    if (Object.keys(store.cursors).length) {
      storage.setStorageSync(STORAGE_KEY, store);
    } else {
      storage.removeStorageSync(STORAGE_KEY);
    }
  } catch (error) {
    console.warn("write memoir draft job state failed", error);
  }
}

export function readMemoirDraftJobCursor(
  projectId: string,
  chapterId: string,
): MemoirDraftJobCursor | null {
  return readStore().cursors[cursorKey(projectId, chapterId)] || null;
}

export function writeMemoirDraftJobCursor(cursor: MemoirDraftJobCursor): void {
  const store = readStore();
  store.cursors[cursorKey(cursor.projectId, cursor.chapterId)] = cursor;
  writeStore(store);
}

export function clearMemoirDraftJobCursor(
  projectId: string,
  chapterId: string,
  jobId: string,
): void {
  const store = readStore();
  const key = cursorKey(projectId, chapterId);
  if (store.cursors[key]?.jobId !== jobId) {
    return;
  }
  delete store.cursors[key];
  writeStore(store);
}
