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

const TTS_CHARACTER_SECONDS = 0.18;
const TTS_COMMA_PUNCTUATION_SECONDS = 0.24;
const TTS_MID_PUNCTUATION_SECONDS = 0.3;
const TTS_SENTENCE_PUNCTUATION_SECONDS = 0.36;
const TTS_MIN_ESTIMATED_DURATION_SECONDS = 0.8;
const TTS_COMMA_PUNCTUATION_PATTERN = /^[，、,]$/;
const TTS_MID_PUNCTUATION_PATTERN = /^[;；:：]$/;
const TTS_SENTENCE_PUNCTUATION_PATTERN = /^[。！？.!?]$/;
const TTS_COARSE_FALLBACK_SLOW_RATIO = 1.2;

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

function ttsCharacterDurationSeconds(character: string): number {
  if (!character || /\s/.test(character)) {
    return 0;
  }
  if (TTS_SENTENCE_PUNCTUATION_PATTERN.test(character)) {
    return TTS_SENTENCE_PUNCTUATION_SECONDS;
  }
  if (TTS_MID_PUNCTUATION_PATTERN.test(character)) {
    return TTS_MID_PUNCTUATION_SECONDS;
  }
  if (TTS_COMMA_PUNCTUATION_PATTERN.test(character)) {
    return TTS_COMMA_PUNCTUATION_SECONDS;
  }
  return TTS_CHARACTER_SECONDS;
}

export function estimateTTSPlaybackDurationSeconds(text: string): number {
  const characterDurations = Array.from(text || "").map((character) => ttsCharacterDurationSeconds(character));
  const estimatedSeconds = characterDurations.reduce((total, duration) => total + duration, 0);
  if (!estimatedSeconds) {
    return 0;
  }
  return Math.max(TTS_MIN_ESTIMATED_DURATION_SECONDS, Math.round(estimatedSeconds * 100) / 100);
}

function splitTextByTTSWeightedPlaybackProgress(text: string, progressRatio: number): TTSProgressText {
  const characters = Array.from(text || "");
  if (!characters.length) {
    return splitTextByPlaybackProgress(text, progressRatio);
  }

  const clampedRatio = Math.max(0, Math.min(1, Number.isFinite(progressRatio) ? progressRatio : 0));
  const characterDurations = characters.map((character) => ttsCharacterDurationSeconds(character));
  const totalDuration = characterDurations.reduce((total, duration) => total + duration, 0);
  if (!totalDuration) {
    return splitTextByPlaybackProgress(text, clampedRatio);
  }

  const elapsedDuration = clampedRatio * totalDuration;
  if (elapsedDuration >= totalDuration) {
    return {
      spokenText: characters.join(""),
      currentText: "",
      remainingText: "",
      progressPercent: 100,
    };
  }

  let activeIndex = 0;
  let accumulatedDuration = 0;
  for (let index = 0; index < characters.length; index += 1) {
    const characterDuration = characterDurations[index];
    if (!characterDuration) {
      activeIndex = index + 1;
      continue;
    }
    const nextDuration = accumulatedDuration + characterDuration;
    if (elapsedDuration < nextDuration) {
      activeIndex = index;
      break;
    }
    accumulatedDuration = nextDuration;
    activeIndex = index + 1;
  }

  const spokenLength = Math.max(0, Math.min(activeIndex, characters.length));
  const hasCurrent = spokenLength < characters.length;
  return {
    spokenText: characters.slice(0, spokenLength).join(""),
    currentText: hasCurrent ? characters[spokenLength] : "",
    remainingText: hasCurrent ? characters.slice(spokenLength + 1).join("") : "",
    progressPercent: Math.round(clampedRatio * 100),
  };
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

  const estimatedDurationSeconds = options.estimateMissingDuration ? estimateTTSPlaybackDurationSeconds(text) : 0;
  const fallbackDurationSeconds = positiveFiniteSeconds(options.fallbackDurationSeconds);
  const resolvedFallbackSeconds = fallbackDurationSeconds && estimatedDurationSeconds
    ? (fallbackDurationSeconds > estimatedDurationSeconds * TTS_COARSE_FALLBACK_SLOW_RATIO
        ? estimatedDurationSeconds
        : fallbackDurationSeconds)
    : fallbackDurationSeconds || estimatedDurationSeconds;
  const resolvedDurationSeconds = positiveFiniteSeconds(durationSeconds) || resolvedFallbackSeconds;
  const progressRatio = resolvedDurationSeconds > 0 ? currentSeconds / resolvedDurationSeconds : 0;
  return splitTextByTTSWeightedPlaybackProgress(text, progressRatio);
}
