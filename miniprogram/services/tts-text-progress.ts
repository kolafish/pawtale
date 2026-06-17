export interface TTSProgressText {
  spokenText: string;
  currentText: string;
  remainingText: string;
  progressPercent: number;
}

export interface TTSAlignmentSegment {
  text: string;
  start_ms: number;
  end_ms: number;
  text_start: number;
  text_end: number;
}

export interface TTSAudioPlaybackProgressOptions {
  alignment?: TTSAlignmentSegment[];
  fallbackDurationSeconds?: number | null;
  estimateMissingDuration?: boolean;
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

function validAlignmentSegment(segment: TTSAlignmentSegment, textLength: number): boolean {
  return Number.isFinite(segment.start_ms)
    && Number.isFinite(segment.end_ms)
    && Number.isFinite(segment.text_start)
    && Number.isFinite(segment.text_end)
    && segment.start_ms >= 0
    && segment.end_ms > segment.start_ms
    && segment.text_start >= 0
    && segment.text_end > segment.text_start
    && segment.text_start < textLength;
}

export function splitTextByTimedAlignment(
  text: string,
  currentTimeMs: number,
  alignment: TTSAlignmentSegment[] = [],
): TTSProgressText {
  const characters = Array.from(text || "");
  if (!characters.length || !alignment.length) {
    return splitTextByPlaybackProgress(text, 0);
  }

  const segments = alignment
    .filter((segment) => validAlignmentSegment(segment, characters.length))
    .map((segment) => ({
      ...segment,
      text_end: Math.min(segment.text_end, characters.length),
    }))
    .sort((left, right) => left.start_ms - right.start_ms || left.text_start - right.text_start);
  if (!segments.length) {
    return splitTextByPlaybackProgress(text, 0);
  }

  const clampedTimeMs = Math.max(0, Number.isFinite(currentTimeMs) ? currentTimeMs : 0);
  const lastSegment = segments[segments.length - 1];
  if (clampedTimeMs >= lastSegment.end_ms) {
    return {
      spokenText: characters.join(""),
      currentText: "",
      remainingText: "",
      progressPercent: 100,
    };
  }

  let activeSegment = segments[0];
  for (let index = 0; index < segments.length; index += 1) {
    const segment = segments[index];
    if (clampedTimeMs < segment.start_ms) {
      activeSegment = index === 0 ? segment : segments[index - 1];
      break;
    }
    if (clampedTimeMs < segment.end_ms) {
      activeSegment = segment;
      break;
    }
  }
  const spokenLength = Math.max(0, Math.min(activeSegment.text_start, characters.length));
  const currentEnd = Math.max(spokenLength + 1, Math.min(activeSegment.text_end, characters.length));
  const progressPercent = Math.round(Math.min(1, clampedTimeMs / Math.max(1, lastSegment.end_ms)) * 100);
  return {
    spokenText: characters.slice(0, spokenLength).join(""),
    currentText: characters.slice(spokenLength, currentEnd).join(""),
    remainingText: characters.slice(currentEnd).join(""),
    progressPercent,
  };
}

function positiveFiniteSeconds(value: number | null | undefined): number {
  return Number.isFinite(value) && Number(value) > 0 ? Number(value) : 0;
}

export function estimateTTSPlaybackDurationSeconds(text: string): number {
  const spokenCharacters = Array.from((text || "").replace(/\s/g, ""));
  if (!spokenCharacters.length) {
    return 0;
  }
  return Math.max(1.2, spokenCharacters.length * 0.28);
}

export function splitTextByAudioPlaybackProgress(
  text: string,
  currentTimeSeconds: number,
  durationSeconds: number,
  options: TTSAudioPlaybackProgressOptions = {},
): TTSProgressText {
  const currentSeconds = Math.max(0, Number.isFinite(currentTimeSeconds) ? currentTimeSeconds : 0);
  const alignment = options.alignment || [];
  if (alignment.length) {
    return splitTextByTimedAlignment(text, currentSeconds * 1000, alignment);
  }

  const resolvedDurationSeconds = positiveFiniteSeconds(durationSeconds)
    || positiveFiniteSeconds(options.fallbackDurationSeconds)
    || (options.estimateMissingDuration ? estimateTTSPlaybackDurationSeconds(text) : 0);
  const progressRatio = resolvedDurationSeconds > 0 ? currentSeconds / resolvedDurationSeconds : 0;
  return splitTextByPlaybackProgress(text, progressRatio);
}
