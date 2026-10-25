import { readMemoirStore } from "./session";
import { readBackendConfig, readSessionCookie } from "../services/request";

const KEY = "pawtale_shared_reading_v1";
export interface SharedReadingProgress {
  versionId: string;
  chapterId: string;
  chapterIndex: number;
  scrollTop: number;
  audioChapterId: string;
  audioHash: string;
  audioIndex: number;
  audioSeconds: number;
}
interface ProgressStore { schema: 1; entries: Record<string, SharedReadingProgress>; }

export function sharedReadingKey(objectId: string, scope: string): string {
  const user = readSessionCookie() ? readMemoirStore().cache_identity?.user_id : "";
  return [readBackendConfig().httpBaseUrl, user || "guest", scope, objectId].join(":");
}

export function readSharedReadingProgress(key: string): SharedReadingProgress | null {
  const store = wx.getStorageSync<ProgressStore>(KEY);
  const value = store?.schema === 1 ? store.entries?.[key] : null;
  if (!value || typeof value.versionId !== "string" || typeof value.chapterId !== "string"
    || typeof value.audioChapterId !== "string" || typeof value.audioHash !== "string"
    || ![value.chapterIndex, value.scrollTop, value.audioIndex, value.audioSeconds].every(n => Number.isFinite(n) && n >= 0)) return null;
  return value;
}

export function saveSharedReadingProgress(key: string, value: SharedReadingProgress): void {
  try {
    const store = wx.getStorageSync<ProgressStore>(KEY);
    const entries = store?.schema === 1 ? { ...store.entries } : {};
    delete entries[key];
    entries[key] = value;
    const recent = Object.fromEntries(Object.entries(entries).slice(-30));
    wx.setStorageSync(KEY, { schema: 1, entries: recent });
  } catch { /* Storage exhaustion must not interrupt reading or playback. */ }
}
