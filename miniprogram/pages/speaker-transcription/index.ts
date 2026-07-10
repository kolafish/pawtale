import type {
  SpeakerTranscriptionConnectionStatus,
  SpeakerTranscriptionGatewayEvent,
} from "../../services/speaker-transcription";
import { readAccessibilitySettings } from "../../services/accessibility-settings";
import {
  SpeakerTranscriptionClient,
  speakerTitle,
} from "../../services/speaker-transcription";
import {
  proofreadSpeakerTranscriptLine,
  type SpeakerProofreadContextLine,
} from "../../services/speaker-transcript-proofreading";
import { ensureRecorderPrivacyAuthorization } from "../../services/wechat-privacy";

interface SpeakerLineView {
  id: string;
  text: string;
  sourceText?: string;
  lineClass: string;
  textClass: string;
  previewEnabled: boolean;
  metaText: string;
  startMs: number | null;
  endMs: number | null;
  proofreadStatusText: string;
  proofreadLoading: boolean;
}

interface ConversationLineView extends SpeakerLineView {
  speakerId: string;
  speakerTitle: string;
  speakerBadgeClass: string;
  speakerToneClass: string;
  domId: string;
}

interface SpeakerWindowView {
  speakerId: string;
  title: string;
  speakerToneClass: string;
  partialText: string;
  partialEndId: string;
  partialScrollClass: string;
  partialScrollTarget: string;
  partialScrollTop: number;
  cumulativeFinalText: string;
  cumulativeFinalStartMs: number | null;
  cumulativeFinalEndMs: number | null;
  lines: SpeakerLineView[];
  segmentCountText: string;
  cardClass: string;
  showNoLines: boolean;
}

interface ActivePartialView {
  visible: boolean;
  speakerId: string;
  speakerTitle: string;
  speakerBadgeClass: string;
  speakerToneClass: string;
  partialBoxClass: string;
  text: string;
  startMs: number | null;
  endMs: number | null;
  partialEndId: string;
  partialScrollClass: string;
  partialScrollTarget: string;
  partialScrollTop: number;
}

interface HistoryLinePreviewView {
  visible: boolean;
  speakerTitle: string;
  speakerToneClass: string;
  metaText: string;
  statusText: string;
  text: string;
}

interface SpeakerTranscriptionPageData {
  pageClass: string;
  controlPanelClass: string;
  showFullControls: boolean;
  statusText: string;
  statusClass: string;
  primaryButtonText: string;
  primaryButtonClass: string;
  recording: boolean;
  busy: boolean;
  error: string;
  speakers: SpeakerWindowView[];
  conversationLines: ConversationLineView[];
  activePartial: ActivePartialView;
  conversationScrollTarget: string;
  showEmpty: boolean;
  elapsedText: string;
  frameCountText: string;
  audioBytesText: string;
  audioLevelStyle: string;
  clearDisabled: boolean;
  showDiarizationNotice: boolean;
  diarizationNotice: string;
  diarizationNoticeClass: string;
  latestFinalLineId: string;
  historyLinePreview: HistoryLinePreviewView;
}

interface TouchPoint {
  clientX: number;
  clientY: number;
}

interface HistoryLineTouch {
  lineId: string;
  startX: number;
  startY: number;
}

interface LineTouchEvent {
  currentTarget: {
    dataset: {
      lineId?: string;
      previewEnabled?: boolean | string;
    };
  };
  touches?: TouchPoint[];
  changedTouches?: TouchPoint[];
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

type FinalizedSpeechLine = Pick<
  ConversationLineView,
  "text" | "sourceText" | "startMs" | "endMs"
>;

const HISTORY_PREVIEW_HOLD_MS = 160;
const HISTORY_PREVIEW_CANCEL_DISTANCE = 18;
const FUZZY_SPEECH_MIN_CHARS = 6;
const FUZZY_SPEECH_MAX_CHARS = 40;
const FUZZY_SPEECH_MAX_LENGTH_RATIO = 1.6;
const FUZZY_SPEECH_MIN_COMMON_OF_SHORTER = 0.68;
const FUZZY_SPEECH_MIN_COMMON_OF_LONGER = 0.48;
const FUZZY_SPEECH_MAX_WINDOW_SPANS = 3;
const LIVE_PARTIAL_FINAL_TIME_PADDING_MS = 1500;
const BEIJING_TIME_OFFSET_MS = 8 * 60 * 60 * 1000;
const UNKNOWN_SPEAKER_ID = "speaker_unknown";
const SPEAKER_TONE_COUNT = 8;
const EMPTY_HISTORY_LINE_PREVIEW: HistoryLinePreviewView = {
  visible: false,
  speakerTitle: "",
  speakerToneClass: "",
  metaText: "",
  statusText: "",
  text: "",
};
const EMPTY_ACTIVE_PARTIAL: ActivePartialView = {
  visible: false,
  speakerId: "",
  speakerTitle: "",
  speakerBadgeClass: "speaker-badge",
  speakerToneClass: "",
  partialBoxClass: "partial-box active-partial",
  text: "",
  startMs: null,
  endMs: null,
  partialEndId: "active-partial-end",
  partialScrollClass: "partial-scroll",
  partialScrollTarget: "",
  partialScrollTop: 0,
};
const SPEAKER_TRANSCRIPTION_STATS_RENDER_INTERVAL_MS = 800;

function statusText(status: SpeakerTranscriptionConnectionStatus): string {
  const texts: Record<SpeakerTranscriptionConnectionStatus, string> = {
    idle: "未开始",
    connecting: "连接中",
    ready: "已连接",
    listening: "实时识别中",
    stopping: "正在停止",
    closed: "已停止",
    error: "出现错误",
  };
  return texts[status];
}

function statusClass(status: SpeakerTranscriptionConnectionStatus): string {
  if (status === "listening") {
    return "status-pill live";
  }
  if (status === "error") {
    return "status-pill error-state";
  }
  return "status-pill";
}

function formatElapsed(ms: number): string {
  const totalSeconds = Math.max(0, Math.floor(ms / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${seconds.toString().padStart(2, "0")}`;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  if (bytes < 1024 * 1024) {
    return `${Math.round(bytes / 1024)} KB`;
  }
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function formatBeijingClockTime(epochMs: number): string {
  const date = new Date(epochMs + BEIJING_TIME_OFFSET_MS);
  const hours = date.getUTCHours().toString().padStart(2, "0");
  const minutes = date.getUTCMinutes().toString().padStart(2, "0");
  const seconds = date.getUTCSeconds().toString().padStart(2, "0");
  return `${hours}:${minutes}:${seconds}`;
}

function formatSegmentTime(event: SpeakerTranscriptionGatewayEvent): string {
  if (typeof event.occurred_start_at_ms === "number" && typeof event.occurred_end_at_ms === "number") {
    const start = formatBeijingClockTime(event.occurred_start_at_ms);
    const end = formatBeijingClockTime(event.occurred_end_at_ms);
    return start === end ? start : `${start}-${end}`;
  }
  if (typeof event.occurred_start_at_ms === "number") {
    return formatBeijingClockTime(event.occurred_start_at_ms);
  }
  if (typeof event.occurred_end_at_ms === "number") {
    return formatBeijingClockTime(event.occurred_end_at_ms);
  }
  return typeof event.start_ms === "number" && typeof event.end_ms === "number"
    ? `${formatElapsed(event.start_ms)}-${formatElapsed(event.end_ms)}`
    : "";
}

function formatSegmentMeta(event: SpeakerTranscriptionGatewayEvent): string {
  const range = formatSegmentTime(event);
  const confidence = typeof event.confidence === "number"
    ? `置信度 ${Math.round(event.confidence * 100)}%`
    : "";
  return [range, confidence].filter(Boolean).join(" · ");
}

function speakerDomId(speakerId: string): string {
  return speakerId.replace(/[^a-zA-Z0-9_-]/g, "_");
}

function stableLineIdForEvent(speakerId: string, event: SpeakerTranscriptionGatewayEvent, fallbackId: string): string {
  const segmentId = (event.segment_id || "").trim();
  return segmentId ? `${speakerDomId(speakerId)}-${speakerDomId(segmentId)}` : fallbackId;
}

function lineDomId(lineId: string): string {
  return `conversation-${speakerDomId(lineId)}`;
}

function speakerTranscriptionPageClass(largeTextEnabled = readAccessibilitySettings().largeTextEnabled): string {
  return largeTextEnabled ? "speaker-transcription-page large-text" : "speaker-transcription-page";
}

function speakerTranscriptionControlPanelClass(hasTranscript: boolean): string {
  return hasTranscript ? "control-panel compact" : "control-panel";
}

function normalizeSpeakerGender(value: string | null | undefined): "male" | "female" | "" {
  const text = (value || "").trim().toLowerCase();
  if (["male", "man", "m", "男", "男声", "男性"].includes(text)) {
    return "male";
  }
  if (["female", "woman", "f", "女", "女声", "女性"].includes(text)) {
    return "female";
  }
  return "";
}

function knownSpeakerDisplayIndex(speakerId: string, speakerOrder: string[]): number {
  if (speakerId === UNKNOWN_SPEAKER_ID) {
    return 0;
  }
  const knownSpeakerIndex = speakerOrder
    .filter((id) => id !== UNKNOWN_SPEAKER_ID)
    .indexOf(speakerId);
  return knownSpeakerIndex >= 0 ? knownSpeakerIndex + 1 : 0;
}

function speakerToneClass(speakerId: string, speakerOrder: string[]): string {
  const displayIndex = knownSpeakerDisplayIndex(speakerId, speakerOrder);
  return displayIndex > 0 ? `tone-${(displayIndex - 1) % SPEAKER_TONE_COUNT}` : "";
}

function classNames(...values: string[]): string {
  return values.filter(Boolean).join(" ");
}

function speakerBadgeClass(toneClass: string): string {
  return classNames("speaker-badge", toneClass);
}

function partialBoxClass(toneClass: string): string {
  return classNames("partial-box active-partial", toneClass);
}

function genderSpeakerTitle(gender: string, genderIndex: number): string {
  if (gender === "male") {
    return `男${genderIndex}`;
  }
  if (gender === "female") {
    return `女${genderIndex}`;
  }
  return "";
}

function eventStartMs(event: SpeakerTranscriptionGatewayEvent): number | null {
  return typeof event.start_ms === "number" ? event.start_ms : null;
}

function eventEndMs(event: SpeakerTranscriptionGatewayEvent): number | null {
  return typeof event.end_ms === "number" ? event.end_ms : null;
}

function incrementalSpeakerText(text: string, previousText: string, sameCumulativeRun: boolean): string {
  const cleanText = text.trim();
  const cleanPrevious = previousText.trim();
  if (!cleanText || !cleanPrevious || !sameCumulativeRun) {
    return cleanText;
  }
  if (cleanText.startsWith(cleanPrevious)) {
    return cleanText.slice(cleanPrevious.length).trim();
  }
  if (cleanPrevious.startsWith(cleanText)) {
    return "";
  }
  return cleanText;
}

function hasReliableDistinctStart(eventStart: number | null, previousEnd: number | null): boolean {
  return eventStart !== null && previousEnd !== null && eventStart >= previousEnd;
}

function isCumulativeSpeakerText(
  text: string,
  previousText: string,
  eventStart: number | null,
  previousStart: number | null,
  previousEnd: number | null,
): boolean {
  const cleanText = text.trim();
  const cleanPrevious = previousText.trim();
  if (!cleanText || !cleanPrevious) {
    return false;
  }
  if (previousStart !== null && eventStart === previousStart) {
    return true;
  }
  if (hasReliableDistinctStart(eventStart, previousEnd)) {
    return false;
  }
  return cleanText.startsWith(cleanPrevious) || cleanPrevious.startsWith(cleanText);
}

function finalLineFromSpeakerEvent(
  event: SpeakerTranscriptionGatewayEvent,
  existing: SpeakerWindowView,
  fallbackId: string,
): SpeakerLineView | null {
  const text = (event.text || "").trim();
  if (!text) {
    return null;
  }
  const eventStart = eventStartMs(event);
  const sameCumulativeRun = isCumulativeSpeakerText(
    text,
    existing.cumulativeFinalText,
    eventStart,
    existing.cumulativeFinalStartMs,
    existing.cumulativeFinalEndMs,
  );
  const incrementalText = incrementalSpeakerText(text, existing.cumulativeFinalText, sameCumulativeRun);
  if (!incrementalText) {
    return null;
  }
  const startMs = sameCumulativeRun && existing.cumulativeFinalText
    ? existing.cumulativeFinalEndMs
    : eventStart;
  return {
    id: fallbackId,
    text: incrementalText,
    sourceText: incrementalText,
    lineClass: "line-item history",
    textClass: "line-text history",
    previewEnabled: true,
    metaText: formatSegmentMeta({
      ...event,
      text: incrementalText,
      start_ms: startMs,
    }),
    startMs,
    endMs: eventEndMs(event),
    proofreadStatusText: "",
    proofreadLoading: false,
  };
}

function lineClasses(
  lineId: string,
  latestLineId: string,
  toneClass = "",
): Pick<SpeakerLineView, "lineClass" | "textClass" | "previewEnabled"> {
  const stateClass = lineId === latestLineId ? "latest" : "history";
  return {
    lineClass: classNames("line-item", stateClass, toneClass),
    textClass: `line-text ${stateClass}`,
    previewEnabled: stateClass === "history",
  };
}

function applyLatestLineClasses(
  speakers: SpeakerWindowView[],
  latestLineId: string,
): SpeakerWindowView[] {
  return speakers.map((speaker) => ({
    ...speaker,
    lines: speaker.lines.map((line) => ({
      ...line,
      ...lineClasses(line.id, latestLineId, speaker.speakerToneClass),
    })),
  }));
}

function conversationLineFromSpeakerLine(
  speakerId: string,
  speakerTitle: string,
  speakerClass: string,
  speakerTone: string,
  line: SpeakerLineView,
): ConversationLineView {
  return {
    ...line,
    speakerId,
    speakerTitle,
    speakerBadgeClass: speakerClass,
    speakerToneClass: speakerTone,
    domId: lineDomId(line.id),
  };
}

function patchConversationLine(
  lines: ConversationLineView[],
  lineId: string,
  patch: Partial<ConversationLineView>,
): ConversationLineView[] {
  return lines.map((line) => (
    line.id === lineId ? { ...line, ...patch } : line
  ));
}

function applyLatestConversationLineClasses(
  lines: ConversationLineView[],
  latestLineId: string,
): ConversationLineView[] {
  return lines.map((line) => ({
    ...line,
    ...lineClasses(line.id, latestLineId, line.speakerToneClass),
  }));
}

function isPreviewEnabled(value: boolean | string | undefined): boolean {
  return value === true || value === "true";
}

function touchPoint(event: LineTouchEvent): TouchPoint | null {
  const point = event.touches?.[0] || event.changedTouches?.[0];
  return point || null;
}

function historyLinePreview(
  lines: ConversationLineView[],
  lineId: string,
  latestLineId: string,
): HistoryLinePreviewView {
  const line = lines.find((item) => item.id === lineId);
  if (!line || line.id === latestLineId || !line.text.trim()) {
    return EMPTY_HISTORY_LINE_PREVIEW;
  }
  return {
    visible: true,
    speakerTitle: line.speakerTitle,
    speakerToneClass: line.speakerToneClass,
    metaText: line.metaText,
    statusText: line.proofreadStatusText,
    text: line.text,
  };
}

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

function removeConversationTextFromUnknownPartial(
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

function activePartialAfterConversationLines(
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
    partialScrollClass: partialIsLong ? "partial-scroll long" : "partial-scroll",
    partialScrollTarget: partialIsLong ? activePartial.partialScrollTarget : "",
    partialScrollTop: partialIsLong ? activePartial.partialScrollTop : 0,
  };
}

function speakerProofreadContextLines(
  conversationLines: ConversationLineView[],
  limit = 6,
): SpeakerProofreadContextLine[] {
  const lines = conversationLines.map((line) => ({
    speakerId: line.speakerId,
    text: line.text.trim(),
    startMs: line.startMs,
    endMs: line.endMs,
    sortMs: line.endMs ?? line.startMs ?? 0,
  }));
  return lines
    .filter((line) => line.text)
    .sort((left, right) => left.sortMs - right.sortMs)
    .slice(-limit)
    .map(({ speakerId, text, startMs, endMs }) => ({
      speakerId,
      text,
      startMs,
      endMs,
    }));
}

function patchSpeakerLine(
  speakers: SpeakerWindowView[],
  speakerId: string,
  lineId: string,
  patch: Partial<SpeakerLineView>,
): SpeakerWindowView[] {
  return speakers.map((speaker) => {
    if (speaker.speakerId !== speakerId) {
      return speaker;
    }
    return {
      ...speaker,
      lines: speaker.lines.map((line) => (
        line.id === lineId ? { ...line, ...patch } : line
      )),
    };
  });
}

function speakerWindow(
  speakerId: string,
  speakerIndex: number,
  patch: Partial<SpeakerWindowView> = {},
): SpeakerWindowView {
  const lines = patch.lines || [];
  const partialEndId = patch.partialEndId || `${speakerDomId(speakerId)}-partial-end`;
  return {
    speakerId,
    title: speakerTitle(speakerId, speakerIndex),
    speakerToneClass: "",
    partialText: "",
    partialEndId,
    partialScrollClass: "partial-scroll",
    partialScrollTarget: "",
    partialScrollTop: 0,
    cumulativeFinalText: "",
    cumulativeFinalStartMs: null,
    cumulativeFinalEndMs: null,
    lines,
    segmentCountText: `${lines.length} 段`,
    cardClass: "speaker-window",
    showNoLines: !patch.partialText && lines.length === 0,
    ...patch,
  };
}

function pagePatch(
  status: SpeakerTranscriptionConnectionStatus,
  hasTranscript: boolean,
): Pick<
  SpeakerTranscriptionPageData,
  | "controlPanelClass"
  | "showFullControls"
  | "statusText"
  | "statusClass"
  | "primaryButtonText"
  | "primaryButtonClass"
  | "recording"
  | "busy"
  | "showEmpty"
  | "clearDisabled"
> {
  const recording = status === "listening";
  const busy = status === "connecting" || status === "stopping";
  return {
    controlPanelClass: speakerTranscriptionControlPanelClass(hasTranscript),
    showFullControls: !hasTranscript,
    statusText: statusText(status),
    statusClass: statusClass(status),
    primaryButtonText: recording ? "停止识别" : (busy ? statusText(status) : "开始识别"),
    primaryButtonClass: recording ? "primary-button stop" : "primary-button",
    recording,
    busy,
    showEmpty: !hasTranscript,
    clearDisabled: busy || recording || !hasTranscript,
  };
}

Page<SpeakerTranscriptionPageData, {
  client: SpeakerTranscriptionClient | null;
  lastStatsRenderMs: number;
  lastPartialSignature: string;
  speakerOrder: string[];
  speakerGenderById: Record<string, "male" | "female" | "">;
  genderSpeakerOrder: Record<"male" | "female", string[]>;
  historyPreviewTimer: number | null;
  historyPreviewTouch: HistoryLineTouch | null;
  onLoad(): void;
  onShow(): void;
  onUnload(): void;
  refreshAccessibilitySettings(): void;
  setStatus(status: SpeakerTranscriptionConnectionStatus): void;
  hasTranscript(): boolean;
  shouldRenderStats(nowMs: number): boolean;
  speakerLabelForEvent(speakerId: string, speakerIndex: number, speakerGender?: string | null): {
    title: string;
    badgeClass: string;
    toneClass: string;
  };
  speakerLabelForId(speakerId: string, speakerIndex: number): {
    title: string;
    badgeClass: string;
    toneClass: string;
  };
  refreshConversationSpeakerLabels(lines: ConversationLineView[]): ConversationLineView[];
  handleSpeakerEvent(event: SpeakerTranscriptionGatewayEvent): void;
  handleDiarizationStatus(event: SpeakerTranscriptionGatewayEvent): void;
  handleLineTouchStart(event: LineTouchEvent): void;
  handleLineTouchMove(event: LineTouchEvent): void;
  handleLineTouchEnd(): void;
  handleLineTouchCancel(): void;
  cancelHistoryPreviewTimer(): void;
  showHistoryLinePreview(lineId: string): void;
  hideHistoryLinePreview(): void;
  proofreadFinalLine(
    speakerId: string,
    lineId: string,
    text: string,
    contextLines: SpeakerProofreadContextLine[],
  ): Promise<void>;
  toggleRecording(): Promise<void>;
  clearTranscript(): void;
  goBack(): void;
}>({
  client: null,
  lastStatsRenderMs: 0,
  lastPartialSignature: "",
  speakerOrder: [],
  speakerGenderById: {},
  genderSpeakerOrder: {
    male: [],
    female: [],
  },
  historyPreviewTimer: null as number | null,
  historyPreviewTouch: null as HistoryLineTouch | null,

  data: {
    pageClass: "speaker-transcription-page",
    controlPanelClass: "control-panel",
    showFullControls: true,
    statusText: "未开始",
    statusClass: "status-pill",
    primaryButtonText: "开始识别",
    primaryButtonClass: "primary-button",
    recording: false,
    busy: false,
    error: "",
    speakers: [],
    conversationLines: [],
    activePartial: EMPTY_ACTIVE_PARTIAL,
    conversationScrollTarget: "",
    showEmpty: true,
    elapsedText: "0:00",
    frameCountText: "0 帧",
    audioBytesText: "0 B",
    audioLevelStyle: "width: 0%;",
    clearDisabled: true,
    showDiarizationNotice: false,
    diarizationNotice: "",
    diarizationNoticeClass: "diarization-notice",
    latestFinalLineId: "",
    historyLinePreview: EMPTY_HISTORY_LINE_PREVIEW,
  },

  onLoad() {
    this.refreshAccessibilitySettings();
  },

  onShow() {
    this.refreshAccessibilitySettings();
  },

  onUnload() {
    this.cancelHistoryPreviewTimer();
    this.client?.close();
    this.client = null;
  },

  refreshAccessibilitySettings() {
    this.setData({ pageClass: speakerTranscriptionPageClass() });
  },

  setStatus(status) {
    this.setData(pagePatch(status, this.hasTranscript()));
  },

  hasTranscript() {
    return this.data.conversationLines.length > 0 || this.data.activePartial.visible;
  },

  shouldRenderStats(nowMs) {
    if (!this.data.recording) {
      this.lastStatsRenderMs = nowMs;
      return true;
    }
    if (nowMs - this.lastStatsRenderMs < SPEAKER_TRANSCRIPTION_STATS_RENDER_INTERVAL_MS) {
      return false;
    }
    this.lastStatsRenderMs = nowMs;
    return true;
  },

  speakerLabelForEvent(speakerId, speakerIndex, speakerGender) {
    const eventGender = normalizeSpeakerGender(speakerGender);
    if (eventGender && !this.speakerGenderById[speakerId]) {
      this.speakerGenderById[speakerId] = eventGender;
      if (!this.genderSpeakerOrder[eventGender].includes(speakerId)) {
        this.genderSpeakerOrder[eventGender] = [...this.genderSpeakerOrder[eventGender], speakerId];
      }
    }
    return this.speakerLabelForId(speakerId, speakerIndex);
  },

  speakerLabelForId(speakerId, speakerIndex) {
    const gender = this.speakerGenderById[speakerId] || "";
    const toneClass = speakerToneClass(speakerId, this.speakerOrder);
    const displayIndex = knownSpeakerDisplayIndex(speakerId, this.speakerOrder) || speakerIndex;
    if (gender) {
      const genderIndex = this.genderSpeakerOrder[gender].indexOf(speakerId) + 1;
      const title = genderSpeakerTitle(gender, genderIndex || 1);
      if (title) {
        return {
          title,
          badgeClass: speakerBadgeClass(toneClass),
          toneClass,
        };
      }
    }
    return {
      title: speakerTitle(speakerId, displayIndex),
      badgeClass: speakerBadgeClass(toneClass),
      toneClass,
    };
  },

  refreshConversationSpeakerLabels(lines) {
    return lines.map((line) => {
      const speakerIndex = this.speakerOrder.indexOf(line.speakerId) + 1;
      const label = this.speakerLabelForId(line.speakerId, speakerIndex || 1);
      return {
        ...line,
        speakerTitle: label.title,
        speakerBadgeClass: label.badgeClass,
        speakerToneClass: label.toneClass,
      };
    });
  },

  async toggleRecording() {
    if (this.data.busy) {
      return;
    }
    if (this.data.recording) {
      this.client?.stop();
      return;
    }
    this.lastStatsRenderMs = 0;
    this.setData({
      error: "",
      showDiarizationNotice: false,
      diarizationNotice: "",
      diarizationNoticeClass: "diarization-notice",
    });
    try {
      await ensureRecorderPrivacyAuthorization();
      const client = new SpeakerTranscriptionClient({
        onStatus: (status) => this.setStatus(status),
        onStats: (stats) => {
          if (!this.shouldRenderStats(Date.now())) {
            return;
          }
          const levelPercent = Math.round(Math.max(0, Math.min(1, stats.audioLevel)) * 100);
          this.setData({
            elapsedText: formatElapsed(stats.recordingElapsedMs),
            frameCountText: `${stats.frameCount} 帧`,
            audioBytesText: formatBytes(stats.audioBytesSent),
            audioLevelStyle: `width: ${levelPercent}%;`,
          });
        },
        onSpeakerEvent: (event) => this.handleSpeakerEvent(event),
        onDiarizationStatus: (event) => this.handleDiarizationStatus(event),
        onError: (message) => this.setData({ error: message }),
      });
      this.client = client;
      await client.start();
    } catch (error) {
      this.setStatus("error");
      this.setData({ error: error instanceof Error ? error.message : "多人转写启动失败" });
    }
  },

  handleSpeakerEvent(event) {
    const speakerId = event.speaker_id || UNKNOWN_SPEAKER_ID;
    const previousHasTranscript = this.hasTranscript();
    const speakerOrder = this.speakerOrder.includes(speakerId)
      ? this.speakerOrder
      : [...this.speakerOrder, speakerId];
    this.speakerOrder = speakerOrder;
    const speakerIndex = speakerOrder.indexOf(speakerId) + 1;
    const previousSpeakerGender = this.speakerGenderById[speakerId] || "";
    const speakerLabel = this.speakerLabelForEvent(speakerId, speakerIndex, event.speaker_gender);
    const speakerLabelsChanged = previousSpeakerGender !== (this.speakerGenderById[speakerId] || "");
    const speakers = [...this.data.speakers];
    const existingIndex = speakers.findIndex((item) => item.speakerId === speakerId);
    const existing = existingIndex >= 0
      ? speakers[existingIndex]
      : speakerWindow(speakerId, speakerIndex);
    let conversationLines = speakerLabelsChanged
      ? this.refreshConversationSpeakerLabels(this.data.conversationLines)
      : this.data.conversationLines;
    let conversationLinesChanged = speakerLabelsChanged;
    let activePartial = this.data.activePartial;
    let conversationScrollTarget = "";
    const text = event.text || "";
    const nextLines = existing.lines.slice();
    let partialText = existing.partialText;
    let partialEndId = existing.partialEndId;
    let partialScrollClass = existing.partialScrollClass;
    let partialScrollTarget = existing.partialScrollTarget;
    let partialScrollTop = existing.partialScrollTop;
    let cumulativeFinalText = existing.cumulativeFinalText;
    let cumulativeFinalStartMs = existing.cumulativeFinalStartMs;
    let cumulativeFinalEndMs = existing.cumulativeFinalEndMs;
    let lineForProofread: SpeakerLineView | null = null;
    let proofreadContextLines: SpeakerProofreadContextLine[] = [];
    let latestFinalLineId = this.data.latestFinalLineId;
    if (event.type === "speaker.final") {
      this.lastPartialSignature = "";
      const lineId = stableLineIdForEvent(
        speakerId,
        event,
        `${speakerId}-${Date.now()}-${nextLines.length}`,
      );
      const line = finalLineFromSpeakerEvent(event, existing, lineId);
      if (line) {
        let finalLineChanged = false;
        const existingConversationLine = conversationLines.find((item) => item.id === line.id);
        const existingSpeakerLine = nextLines.find((item) => item.id === line.id);
        if (
          existingConversationLine
          && existingConversationLine.text === line.text
          && existingConversationLine.metaText === line.metaText
        ) {
          lineForProofread = null;
        } else if (existingConversationLine || existingSpeakerLine) {
          const previousLine = existingConversationLine || existingSpeakerLine;
          const textChanged = previousLine?.text !== line.text;
          if (textChanged) {
            proofreadContextLines = speakerProofreadContextLines(
              conversationLines.filter((item) => item.id !== line.id),
            );
          }
          const linePatch = {
            ...line,
            proofreadLoading: textChanged
              ? true
              : existingConversationLine?.proofreadLoading || existingSpeakerLine?.proofreadLoading || false,
            proofreadStatusText: textChanged
              ? "校对中"
              : existingConversationLine?.proofreadStatusText || existingSpeakerLine?.proofreadStatusText || "",
          };
          for (let index = 0; index < nextLines.length; index += 1) {
            if (nextLines[index].id === line.id) {
              nextLines[index] = { ...nextLines[index], ...linePatch };
            }
          }
          conversationLines = patchConversationLine(conversationLines, line.id, linePatch);
          if (textChanged) {
            lineForProofread = { ...line, proofreadLoading: true, proofreadStatusText: "校对中" };
          }
          finalLineChanged = true;
        } else {
          proofreadContextLines = speakerProofreadContextLines(conversationLines);
          line.proofreadLoading = true;
          line.proofreadStatusText = "校对中";
          nextLines.unshift(line);
          lineForProofread = line;
          const conversationLine = conversationLineFromSpeakerLine(
            speakerId,
            speakerLabel.title,
            speakerLabel.badgeClass,
            speakerLabel.toneClass,
            line,
          );
          conversationLines = [conversationLine, ...conversationLines];
          finalLineChanged = true;
        }
        if (finalLineChanged) {
          latestFinalLineId = line.id;
          conversationScrollTarget = lineDomId(line.id);
          conversationLinesChanged = true;
        }
      }
      partialText = "";
      partialScrollClass = "partial-scroll";
      partialScrollTarget = "";
      partialScrollTop = 0;
      activePartial = activePartialAfterConversationLines(this.data.activePartial, conversationLines);
      if (activePartial.visible) {
        conversationScrollTarget = "active-partial-card";
      }
      if (text.trim()) {
        cumulativeFinalText = text.trim();
        cumulativeFinalStartMs = eventStartMs(event);
        cumulativeFinalEndMs = eventEndMs(event);
      }
      if (!conversationLinesChanged && !this.data.activePartial.visible && !speakerLabelsChanged) {
        return;
      }
    } else if (event.type === "speaker.partial") {
      const eventStart = eventStartMs(event);
      const sameCumulativeRun = isCumulativeSpeakerText(
        text,
        existing.cumulativeFinalText,
        eventStart,
        existing.cumulativeFinalStartMs,
        existing.cumulativeFinalEndMs,
      );
      partialText = incrementalSpeakerText(text, existing.cumulativeFinalText, sameCumulativeRun);
      if (speakerId === UNKNOWN_SPEAKER_ID) {
        partialText = removeConversationTextFromUnknownPartial(
          partialText,
          conversationLines,
          eventStart,
          eventEndMs(event),
        );
      }
      const partialIsLong = partialText.length > 90;
      partialScrollClass = partialIsLong ? "partial-scroll long" : "partial-scroll";
      if (partialIsLong) {
        if (partialText !== existing.partialText) {
          partialScrollTop += 10000;
          partialEndId = `${speakerDomId(speakerId)}-partial-end-${partialScrollTop}`;
          partialScrollTarget = partialEndId;
        } else {
          partialScrollTarget = "";
        }
      } else {
        partialScrollTarget = "";
        partialScrollTop = 0;
      }
      activePartial = partialText
        ? {
          visible: true,
          speakerId,
          speakerTitle: speakerLabel.title,
          speakerBadgeClass: speakerLabel.badgeClass,
          speakerToneClass: speakerLabel.toneClass,
          partialBoxClass: partialBoxClass(speakerLabel.toneClass),
          text: partialText,
          startMs: eventStart,
          endMs: eventEndMs(event),
          partialEndId,
          partialScrollClass,
          partialScrollTarget,
          partialScrollTop,
        }
        : EMPTY_ACTIVE_PARTIAL;
      conversationScrollTarget = partialText ? "active-partial-card" : "";
      const partialSignature = [
        speakerId,
        event.segment_id || "",
        partialText,
        partialScrollClass,
        String(partialScrollTop),
      ].join("|");
      if (!speakerLabelsChanged && partialSignature === this.lastPartialSignature) {
        return;
      }
      this.lastPartialSignature = partialSignature;
    }
    const nextSpeaker = speakerWindow(speakerId, speakerIndex, {
      ...existing,
      title: speakerLabel.title,
      speakerToneClass: speakerLabel.toneClass,
      partialText,
      partialEndId,
      partialScrollClass,
      partialScrollTarget,
      partialScrollTop,
      cumulativeFinalText,
      cumulativeFinalStartMs,
      cumulativeFinalEndMs,
      lines: nextLines.slice(0, 20),
      segmentCountText: `${nextLines.length} 段`,
      cardClass: partialText ? "speaker-window active" : "speaker-window",
      showNoLines: !partialText && nextLines.length === 0,
    });
    if (existingIndex >= 0) {
      speakers[existingIndex] = nextSpeaker;
    } else {
      speakers.push(nextSpeaker);
    }
    const visibleSpeakers = applyLatestLineClasses(speakers, latestFinalLineId);
    const visibleConversationLines = conversationLinesChanged
      ? this.refreshConversationSpeakerLabels(
        applyLatestConversationLineClasses(conversationLines, latestFinalLineId),
      )
      : this.data.conversationLines;
    const dataPatch: Partial<SpeakerTranscriptionPageData> = {
      speakers: visibleSpeakers,
      activePartial,
      conversationScrollTarget,
      latestFinalLineId,
    };
    const hasTranscript = visibleConversationLines.length > 0 || activePartial.visible;
    if (previousHasTranscript !== hasTranscript) {
      Object.assign(
        dataPatch,
        pagePatch(this.data.recording ? "listening" : "ready", hasTranscript),
      );
    }
    if (conversationLinesChanged) {
      dataPatch.conversationLines = visibleConversationLines;
    }
    this.setData(dataPatch);
    if (lineForProofread) {
      void this.proofreadFinalLine(
        speakerId,
        lineForProofread.id,
        lineForProofread.text,
        proofreadContextLines,
      );
    }
  },

  handleDiarizationStatus(event) {
    const available = event.status === "available";
    this.setData({
      showDiarizationNotice: true,
      diarizationNotice: event.message || (available
        ? "已收到说话人标签，会按不同人标注显示。"
        : "当前 ASR 返回结果没有说话人标签，暂时只能标成未分辨说话人。"),
      diarizationNoticeClass: available ? "diarization-notice ok" : "diarization-notice warning",
    });
  },

  handleLineTouchStart(event) {
    this.cancelHistoryPreviewTimer();
    const { lineId = "", previewEnabled } = event.currentTarget.dataset;
    const point = touchPoint(event);
    if (!lineId || !isPreviewEnabled(previewEnabled) || !point) {
      this.historyPreviewTouch = null;
      return;
    }
    this.historyPreviewTouch = {
      lineId,
      startX: point.clientX,
      startY: point.clientY,
    };
    this.historyPreviewTimer = setTimeout(() => {
      this.historyPreviewTimer = null;
      const pendingTouch = this.historyPreviewTouch;
      if (pendingTouch?.lineId === lineId) {
        this.showHistoryLinePreview(lineId);
      }
    }, HISTORY_PREVIEW_HOLD_MS);
  },

  handleLineTouchMove(event) {
    const pendingTouch = this.historyPreviewTouch;
    const point = touchPoint(event);
    if (!pendingTouch || !point) {
      return;
    }
    const movedX = Math.abs(point.clientX - pendingTouch.startX);
    const movedY = Math.abs(point.clientY - pendingTouch.startY);
    if (movedX > HISTORY_PREVIEW_CANCEL_DISTANCE || movedY > HISTORY_PREVIEW_CANCEL_DISTANCE) {
      this.cancelHistoryPreviewTimer();
      this.historyPreviewTouch = null;
      this.hideHistoryLinePreview();
    }
  },

  handleLineTouchEnd() {
    this.cancelHistoryPreviewTimer();
    this.historyPreviewTouch = null;
    this.hideHistoryLinePreview();
  },

  handleLineTouchCancel() {
    this.cancelHistoryPreviewTimer();
    this.historyPreviewTouch = null;
    this.hideHistoryLinePreview();
  },

  cancelHistoryPreviewTimer() {
    if (this.historyPreviewTimer !== null) {
      clearTimeout(this.historyPreviewTimer);
      this.historyPreviewTimer = null;
    }
  },

  showHistoryLinePreview(lineId) {
    const preview = historyLinePreview(
      this.data.conversationLines,
      lineId,
      this.data.latestFinalLineId,
    );
    this.setData({ historyLinePreview: preview });
  },

  hideHistoryLinePreview() {
    if (this.data.historyLinePreview.visible) {
      this.setData({ historyLinePreview: EMPTY_HISTORY_LINE_PREVIEW });
    }
  },

  async proofreadFinalLine(speakerId, lineId, text, contextLines) {
    try {
      const result = await proofreadSpeakerTranscriptLine(text, contextLines);
      const proofreadText = result.proofread_text.trim();
      const linePatch = {
        text: proofreadText || text,
        proofreadLoading: false,
        proofreadStatusText: "已校对",
      };
      const speakers = applyLatestLineClasses(
        patchSpeakerLine(this.data.speakers, speakerId, lineId, linePatch),
        this.data.latestFinalLineId,
      );
      const conversationLines = this.refreshConversationSpeakerLabels(
        applyLatestConversationLineClasses(
          patchConversationLine(this.data.conversationLines, lineId, linePatch),
          this.data.latestFinalLineId,
        ),
      );
      const activePartial = activePartialAfterConversationLines(this.data.activePartial, conversationLines);
      this.setData({ speakers, conversationLines, activePartial });
    } catch (_error) {
      const linePatch = {
        proofreadLoading: false,
        proofreadStatusText: "校对失败",
      };
      const speakers = applyLatestLineClasses(
        patchSpeakerLine(this.data.speakers, speakerId, lineId, linePatch),
        this.data.latestFinalLineId,
      );
      const conversationLines = this.refreshConversationSpeakerLabels(
        applyLatestConversationLineClasses(
          patchConversationLine(this.data.conversationLines, lineId, linePatch),
          this.data.latestFinalLineId,
        ),
      );
      this.setData({ speakers, conversationLines });
    }
  },

  clearTranscript() {
    if (this.data.recording || this.data.busy) {
      return;
    }
    this.cancelHistoryPreviewTimer();
    this.historyPreviewTouch = null;
    this.speakerOrder = [];
    this.speakerGenderById = {};
    this.genderSpeakerOrder = {
      male: [],
      female: [],
    };
    this.lastPartialSignature = "";
    const speakers: SpeakerWindowView[] = [];
    this.setData({
      speakers,
      conversationLines: [],
      activePartial: EMPTY_ACTIVE_PARTIAL,
      conversationScrollTarget: "",
      elapsedText: "0:00",
      frameCountText: "0 帧",
      audioBytesText: "0 B",
      audioLevelStyle: "width: 0%;",
      showDiarizationNotice: false,
      diarizationNotice: "",
      diarizationNoticeClass: "diarization-notice",
      latestFinalLineId: "",
      historyLinePreview: EMPTY_HISTORY_LINE_PREVIEW,
      ...pagePatch("idle", false),
    });
  },

  goBack() {
    wx.navigateBack({ delta: 1 });
  },
});

export {
  formatBytes as formatSpeakerTranscriptionBytes,
  formatElapsed as formatSpeakerTranscriptionElapsed,
  formatSegmentMeta as formatSpeakerTranscriptionSegmentMeta,
  pagePatch as speakerTranscriptionPagePatch,
  speakerTranscriptionPageClass,
  speakerProofreadContextLines,
  removeFinalTextFromPartialText,
  removeConversationTextFromUnknownPartial,
};
