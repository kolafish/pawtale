export const TRANSCRIPT_NAVIGATION_TARGET_KEY = "pawtale_transcript_navigation_target";

interface TranscriptNavigationTarget {
  utterance_id?: string;
  created_at?: number;
}

function normalizeTarget(value: unknown): string {
  if (typeof value === "string") {
    return value.trim();
  }
  if (value && typeof value === "object") {
    const target = value as TranscriptNavigationTarget;
    return typeof target.utterance_id === "string" ? target.utterance_id.trim() : "";
  }
  return "";
}

export function writeTranscriptNavigationTarget(utteranceId: string): void {
  const targetId = utteranceId.trim();
  if (!targetId) {
    return;
  }
  wx.setStorageSync(TRANSCRIPT_NAVIGATION_TARGET_KEY, {
    utterance_id: targetId,
    created_at: Date.now(),
  });
}

export function readTranscriptNavigationTarget(): string {
  return normalizeTarget(wx.getStorageSync(TRANSCRIPT_NAVIGATION_TARGET_KEY));
}

export function clearTranscriptNavigationTarget(): void {
  wx.removeStorageSync(TRANSCRIPT_NAVIGATION_TARGET_KEY);
}
