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
}

function textWithoutHighlight(text: string, progressPercent = 0): TTSProgressText {
  const characters = Array.from(text || "");
  return {
    spokenText: "",
    currentText: "",
    remainingText: characters.join(""),
    progressPercent,
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
    return textWithoutHighlight(text);
  }

  const segments = alignment
    .filter((segment) => validAlignmentSegment(segment, characters.length))
    .map((segment) => ({
      ...segment,
      text_end: Math.min(segment.text_end, characters.length),
    }))
    .sort((left, right) => left.start_ms - right.start_ms || left.text_start - right.text_start);
  if (!segments.length) {
    return textWithoutHighlight(text);
  }

  const clampedTimeMs = Math.max(0, Number.isFinite(currentTimeMs) ? currentTimeMs : 0);
  const lastSegment = segments[segments.length - 1];
  const progressPercent = Math.round(Math.min(1, clampedTimeMs / Math.max(1, lastSegment.end_ms)) * 100);
  if (clampedTimeMs >= lastSegment.end_ms) {
    return {
      spokenText: characters.join(""),
      currentText: "",
      remainingText: "",
      progressPercent: 100,
    };
  }
  if (clampedTimeMs < segments[0].start_ms) {
    return textWithoutHighlight(text, progressPercent);
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

function playbackProgressPercent(currentSeconds: number, durationSeconds: number): number {
  const duration = positiveFiniteSeconds(durationSeconds);
  if (!duration) {
    return 0;
  }
  const current = Math.max(0, Number.isFinite(currentSeconds) ? currentSeconds : 0);
  return Math.round(Math.max(0, Math.min(1, current / duration)) * 100);
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

  return textWithoutHighlight(text, playbackProgressPercent(currentSeconds, durationSeconds));
}
