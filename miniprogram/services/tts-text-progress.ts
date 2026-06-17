export interface TTSProgressText {
  spokenText: string;
  currentText: string;
  remainingText: string;
  progressPercent: number;
}

export function splitTextByPlaybackProgress(text: string, progressRatio: number): TTSProgressText {
  const characters = Array.from(text || "");
  const clampedRatio = Math.max(0, Math.min(1, Number.isFinite(progressRatio) ? progressRatio : 0));
  const activeIndex = Math.min(characters.length, Math.floor(characters.length * clampedRatio));
  const hasCurrent = activeIndex < characters.length;
  const spokenLength = hasCurrent ? activeIndex : characters.length;
  const currentLength = hasCurrent ? 1 : 0;
  return {
    spokenText: characters.slice(0, spokenLength).join(""),
    currentText: characters.slice(spokenLength, spokenLength + currentLength).join(""),
    remainingText: characters.slice(spokenLength + currentLength).join(""),
    progressPercent: Math.round(clampedRatio * 100),
  };
}
