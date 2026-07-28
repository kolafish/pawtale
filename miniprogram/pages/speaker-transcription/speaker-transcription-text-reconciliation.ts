export interface ActivePartialView {
  visible: boolean;
  speakerId: string;
  speakerTitle: string;
  speakerBadgeClass: string;
  speakerToneClass: string;
  partialBoxClass: string;
  text: string;
  startMs: number | null;
  endMs: number | null;
  partialScrollTop: number;
}

interface ComparableSpeechChar {
  char: string;
  start: number;
  end: number;
}

interface SpeechTextSpan {
  start: number;
  end: number;
  key: string;
}

interface FinalizedSpeechLine {
  text: string;
  sourceText?: string;
  startMs: number | null;
  endMs: number | null;
}

const FUZZY_SPEECH_MIN_CHARS = 6;
const FUZZY_SPEECH_MAX_CHARS = 40;
const FUZZY_SPEECH_MAX_LENGTH_RATIO = 1.6;
const FUZZY_SPEECH_MIN_COMMON_OF_SHORTER = 0.68;
const FUZZY_SPEECH_MIN_COMMON_OF_LONGER = 0.48;
const FUZZY_SPEECH_MAX_WINDOW_SPANS = 3;
const LIVE_PARTIAL_FINAL_TIME_PADDING_MS = 1500;

const UNKNOWN_SPEAKER_ID = "speaker_unknown";

export const EMPTY_ACTIVE_PARTIAL: ActivePartialView = {
  visible: false,
  speakerId: "",
  speakerTitle: "",
  speakerBadgeClass: "speaker-badge",
  speakerToneClass: "",
  partialBoxClass: "partial-box active-partial",
  text: "",
  startMs: null,
  endMs: null,
  partialScrollTop: 0,
};

function isComparableSpeechChar(char: string): boolean {
  return /[0-9A-Za-z\u3400-\u9fff]/.test(char);
}

function comparableSpeechChars(text: string): ComparableSpeechChar[] {
  const chars: ComparableSpeechChar[] = [];
  let offset = 0;
  for (const char of text) {
    const start = offset;
    offset += char.length;
    if (isComparableSpeechChar(char)) {
      chars.push({
        char: char.toLowerCase(),
        start,
        end: offset,
      });
    }
  }
  return chars;
}

function comparableSpeechText(text: string): string {
  return comparableSpeechChars(text).map((item) => item.char).join("");
}

function trimSpeechBoundary(text: string): string {
  return text
    .trim()
    .replace(/^[，。？！、；：,.?!;:]+/, "")
    .trim();
}

function isSpeechBoundaryChar(char: string): boolean {
  return /[\s，。？！、；：,.?!;:]/.test(char);
}

function isSpeechSentenceBoundaryChar(char: string): boolean {
  return /[。？！；.!?;]/.test(char);
}

function consumeFollowingSpeechBoundary(text: string, offset: number): number {
  let nextOffset = offset;
  while (nextOffset < text.length) {
    const char = Array.from(text.slice(nextOffset))[0] || "";
    if (!char || !isSpeechBoundaryChar(char)) {
      break;
    }
    nextOffset += char.length;
  }
  return nextOffset;
}

function speechTextSpans(text: string): SpeechTextSpan[] {
  const spans: SpeechTextSpan[] = [];
  let spanStart = 0;
  let offset = 0;
  while (offset < text.length) {
    const char = Array.from(text.slice(offset))[0] || "";
    if (!char) {
      break;
    }
    const nextOffset = offset + char.length;
    if (!isSpeechSentenceBoundaryChar(char)) {
      offset = nextOffset;
      continue;
    }
    const spanEnd = nextOffset;
    const key = comparableSpeechText(text.slice(spanStart, spanEnd));
    if (key) {
      spans.push({ start: spanStart, end: spanEnd, key });
    }
    spanStart = consumeFollowingSpeechBoundary(text, spanEnd);
    offset = spanStart;
  }
  if (spanStart < text.length) {
    const key = comparableSpeechText(text.slice(spanStart));
    if (key) {
      spans.push({ start: spanStart, end: text.length, key });
    }
  }
  return spans;
}

function commonSpeechSubsequenceLength(left: string, right: string): number {
  if (!left || !right) {
    return 0;
  }
  const previous = new Array(right.length + 1).fill(0);
  const current = new Array(right.length + 1).fill(0);
  for (let leftIndex = 1; leftIndex <= left.length; leftIndex += 1) {
    for (let rightIndex = 1; rightIndex <= right.length; rightIndex += 1) {
      current[rightIndex] = left[leftIndex - 1] === right[rightIndex - 1]
        ? previous[rightIndex - 1] + 1
        : Math.max(previous[rightIndex], current[rightIndex - 1]);
    }
    for (let rightIndex = 0; rightIndex <= right.length; rightIndex += 1) {
      previous[rightIndex] = current[rightIndex];
      current[rightIndex] = 0;
    }
  }
  return previous[right.length];
}

function speechTextSimilarityScore(left: string, right: string): number {
  const shorterLength = Math.min(left.length, right.length);
  const longerLength = Math.max(left.length, right.length);
  if (!shorterLength || !longerLength) {
    return 0;
  }
  const commonLength = commonSpeechSubsequenceLength(left, right);
  return (commonLength / shorterLength) + (commonLength / longerLength);
}

function isLikelySameShortSpeechText(partialKey: string, finalKey: string): boolean {
  const shorterLength = Math.min(partialKey.length, finalKey.length);
  const longerLength = Math.max(partialKey.length, finalKey.length);
  if (shorterLength < FUZZY_SPEECH_MIN_CHARS) {
    return false;
  }
  if (longerLength > FUZZY_SPEECH_MAX_CHARS) {
    return false;
  }
  if (longerLength / shorterLength > FUZZY_SPEECH_MAX_LENGTH_RATIO) {
    return false;
  }
  const commonLength = commonSpeechSubsequenceLength(partialKey, finalKey);
  return commonLength / shorterLength >= FUZZY_SPEECH_MIN_COMMON_OF_SHORTER
    && commonLength / longerLength >= FUZZY_SPEECH_MIN_COMMON_OF_LONGER;
}

function removeLikelyFinalSpeechWindow(partialText: string, finalKey: string): string | null {
  const spans = speechTextSpans(partialText);
  let bestMatch: { start: number; end: number; score: number } | null = null;
  for (let startIndex = 0; startIndex < spans.length; startIndex += 1) {
    let windowKey = "";
    const maxEndIndex = Math.min(spans.length, startIndex + FUZZY_SPEECH_MAX_WINDOW_SPANS);
    for (let endIndex = startIndex; endIndex < maxEndIndex; endIndex += 1) {
      windowKey += spans[endIndex].key;
      if (windowKey.length > FUZZY_SPEECH_MAX_CHARS) {
        break;
      }
      if (!isLikelySameShortSpeechText(windowKey, finalKey)) {
        continue;
      }
      const score = speechTextSimilarityScore(windowKey, finalKey);
      const candidate = {
        start: spans[startIndex].start,
        end: spans[endIndex].end,
        score,
      };
      const expandsSameRightEdge = bestMatch
        && candidate.end === bestMatch.end
        && candidate.start < bestMatch.start;
      const shrinksSameRightEdge = bestMatch
        && candidate.end === bestMatch.end
        && candidate.start > bestMatch.start;
      if (!bestMatch || expandsSameRightEdge || (!shrinksSameRightEdge && score > bestMatch.score)) {
        bestMatch = {
          ...candidate,
        };
      }
    }
  }
  if (!bestMatch) {
    return null;
  }
  const end = consumeFollowingSpeechBoundary(partialText, bestMatch.end);
  return trimSpeechBoundary(`${partialText.slice(0, bestMatch.start)}${partialText.slice(end)}`);
}

function removeFinalTextFromPartialText(partialText: string, finalText: string): string {
  const cleanPartial = partialText.trim();
  const partialChars = comparableSpeechChars(cleanPartial);
  const finalKey = comparableSpeechText(finalText);
  if (partialChars.length === 0) {
    return "";
  }
  if (!finalKey) {
    return cleanPartial;
  }
  const partialKey = partialChars.map((item) => item.char).join("");
  if (finalKey === partialKey || finalKey.endsWith(partialKey)) {
    return "";
  }
  const matchIndex = partialKey.indexOf(finalKey);
  if (matchIndex < 0) {
    const textWithoutLikelyWindow = removeLikelyFinalSpeechWindow(cleanPartial, finalKey);
    if (textWithoutLikelyWindow !== null) {
      return textWithoutLikelyWindow;
    }
    if (isLikelySameShortSpeechText(partialKey, finalKey)) {
      return "";
    }
    return cleanPartial;
  }
  const matchEndsPartial = matchIndex + finalKey.length >= partialKey.length;
  const matchStartsPartial = matchIndex === 0;
  if (!matchStartsPartial && !matchEndsPartial && finalKey.length < 4) {
    return cleanPartial;
  }
  const start = partialChars[matchIndex].start;
  const end = consumeFollowingSpeechBoundary(
    cleanPartial,
    partialChars[matchIndex + finalKey.length - 1].end,
  );
  return trimSpeechBoundary(`${cleanPartial.slice(0, start)}${cleanPartial.slice(end)}`);
}

function lineMayOverlapPartial(
  line: FinalizedSpeechLine,
  partialStartMs: number | null,
  partialEndMs: number | null,
): boolean {
  const lineStartMs = line.startMs;
  const lineEndMs = line.endMs;
  if (partialStartMs === null || partialEndMs === null || lineStartMs === null || lineEndMs === null) {
    return true;
  }
  return partialStartMs - LIVE_PARTIAL_FINAL_TIME_PADDING_MS <= lineEndMs
    && lineStartMs <= partialEndMs + LIVE_PARTIAL_FINAL_TIME_PADDING_MS;
}

export function removeConversationTextFromUnknownPartial(
  partialText: string,
  conversationLines: FinalizedSpeechLine[],
  partialStartMs: number | null = null,
  partialEndMs: number | null = null,
): string {
  return conversationLines
    .filter((line) => lineMayOverlapPartial(line, partialStartMs, partialEndMs))
    .slice()
    .reverse()
    .reduce((text, line) => {
      const withoutSource = removeFinalTextFromPartialText(text, line.sourceText || line.text);
      if (line.sourceText && line.sourceText !== line.text) {
        return removeFinalTextFromPartialText(withoutSource, line.text);
      }
      return withoutSource;
    }, partialText.trim());
}

export function activePartialAfterConversationLines(
  activePartial: ActivePartialView,
  conversationLines: FinalizedSpeechLine[],
): ActivePartialView {
  if (!activePartial.visible) {
    return EMPTY_ACTIVE_PARTIAL;
  }
  if (activePartial.speakerId && activePartial.speakerId !== UNKNOWN_SPEAKER_ID) {
    return EMPTY_ACTIVE_PARTIAL;
  }
  const nextText = removeConversationTextFromUnknownPartial(
    activePartial.text,
    conversationLines,
    activePartial.startMs,
    activePartial.endMs,
  );
  if (!nextText) {
    return EMPTY_ACTIVE_PARTIAL;
  }
  if (nextText === activePartial.text.trim()) {
    return activePartial;
  }
  const partialIsLong = nextText.length > 90;
  return {
    ...activePartial,
    text: nextText,
    partialScrollTop: partialIsLong ? activePartial.partialScrollTop : 0,
  };
}
