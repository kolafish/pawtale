export interface TTSProgressText {
  spokenText: string;
  remainingText: string;
  progressPercent: number;
}

export function splitTextByPlaybackProgress(text: string, progressRatio: number): TTSProgressText {
  const characters = Array.from(text || "");
  const clampedRatio = Math.max(0, Math.min(1, Number.isFinite(progressRatio) ? progressRatio : 0));
  const spokenLength = Math.min(characters.length, Math.floor(characters.length * clampedRatio));
  return {
    spokenText: characters.slice(0, spokenLength).join(""),
    remainingText: characters.slice(spokenLength).join(""),
    progressPercent: Math.round(clampedRatio * 100),
  };
}
