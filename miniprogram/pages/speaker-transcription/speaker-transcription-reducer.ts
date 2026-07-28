import type { SpeakerProofreadContextLine } from "../../services/speaker-transcript-proofreading";
import {
  speakerTitle,
  type SpeakerTranscriptionConnectionStatus,
  type SpeakerTranscriptionGatewayEvent,
} from "../../services/speaker-transcription";

import {
  activePartialAfterConversationLines,
  EMPTY_ACTIVE_PARTIAL,
  removeConversationTextFromUnknownPartial,
  type ActivePartialView,
} from "./speaker-transcription-text-reconciliation";

export interface SpeakerLineView {
  id: string;
  text: string;
  sourceText?: string;
  lineClass: string;
  textClass: string;
  metaText: string;
  startMs: number | null;
  endMs: number | null;
  proofreadStatusText: string;
  proofreadLoading: boolean;
}

export interface ConversationLineView extends SpeakerLineView {
  speakerId: string;
  speakerTitle: string;
  speakerBadgeClass: string;
  speakerToneClass: string;
  domId: string;
}

export interface SpeakerStreamState {
  partialText: string;
  partialScrollTop: number;
  cumulativeFinalText: string;
  cumulativeFinalStartMs: number | null;
  cumulativeFinalEndMs: number | null;
}

export interface SpeakerTranscriptionPageData {
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
  conversationLines: ConversationLineView[];
  activePartial: ActivePartialView;
  conversationScrollTarget: string;
  showEmpty: boolean;
  audioLevelStyle: string;
  clearDisabled: boolean;
  showDiarizationNotice: boolean;
  diarizationNotice: string;
  diarizationNoticeClass: string;
  latestFinalLineId: string;
}

const BEIJING_TIME_OFFSET_MS = 8 * 60 * 60 * 1000;
const UNKNOWN_SPEAKER_ID = "speaker_unknown";
const SPEAKER_TONE_COUNT = 8;
export const SPEAKER_TRANSCRIPTION_LEVEL_RENDER_INTERVAL_MS = 800;

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

export function speakerTranscriptionPageClass(largeTextEnabled: boolean): string {
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
  existing: SpeakerStreamState,
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
): Pick<SpeakerLineView, "lineClass" | "textClass"> {
  const stateClass = lineId === latestLineId ? "latest" : "history";
  return {
    lineClass: classNames("line-item", stateClass, toneClass),
    textClass: `line-text ${stateClass}`,
  };
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

function speakerStreamState(): SpeakerStreamState {
  return {
    partialText: "",
    partialScrollTop: 0,
    cumulativeFinalText: "",
    cumulativeFinalStartMs: null,
    cumulativeFinalEndMs: null,
  };
}

export function speakerTranscriptionPagePatch(
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

export interface SpeakerTranscriptionReducerState {
  conversationLines: ConversationLineView[];
  activePartial: ActivePartialView;
  conversationScrollTarget: string;
  latestFinalLineId: string;
  lastPartialSignature: string;
  streamStateBySpeakerId: Record<string, SpeakerStreamState>;
  speakerOrder: string[];
  speakerGenderById: Record<string, "male" | "female" | "">;
  genderSpeakerOrder: Record<"male" | "female", string[]>;
}

export interface SpeakerTranscriptionProofreadRequest {
  lineId: string;
  text: string;
  contextLines: SpeakerProofreadContextLine[];
}

export interface SpeakerTranscriptionReduceResult {
  state: SpeakerTranscriptionReducerState;
  patch: Partial<SpeakerTranscriptionPageData> | null;
  hasTranscriptChanged: boolean;
  hasTranscript: boolean;
  proofreadRequest: SpeakerTranscriptionProofreadRequest | null;
}

export function initialSpeakerTranscriptionReducerState(): SpeakerTranscriptionReducerState {
  return {
    conversationLines: [],
    activePartial: EMPTY_ACTIVE_PARTIAL,
    conversationScrollTarget: "",
    latestFinalLineId: "",
    lastPartialSignature: "",
    streamStateBySpeakerId: {},
    speakerOrder: [],
    speakerGenderById: {},
    genderSpeakerOrder: {
      male: [],
      female: [],
    },
  };
}

export function resetSpeakerTranscriptionStream(
  state: SpeakerTranscriptionReducerState,
): SpeakerTranscriptionReducerState {
  return {
    ...state,
    lastPartialSignature: "",
    streamStateBySpeakerId: {},
  };
}

export function speakerTranscriptionHasTranscript(
  state: Pick<SpeakerTranscriptionReducerState, "conversationLines" | "activePartial">,
): boolean {
  return state.conversationLines.length > 0 || state.activePartial.visible;
}

export function speakerTranscriptionViewPatch(
  state: SpeakerTranscriptionReducerState,
): Pick<
  SpeakerTranscriptionPageData,
  "conversationLines" | "activePartial" | "conversationScrollTarget" | "latestFinalLineId"
> {
  return {
    conversationLines: state.conversationLines,
    activePartial: state.activePartial,
    conversationScrollTarget: state.conversationScrollTarget,
    latestFinalLineId: state.latestFinalLineId,
  };
}

function speakerLabelForId(
  state: SpeakerTranscriptionReducerState,
  speakerId: string,
  speakerIndex: number,
): { title: string; badgeClass: string; toneClass: string } {
  const gender = state.speakerGenderById[speakerId] || "";
  const toneClass = speakerToneClass(speakerId, state.speakerOrder);
  const displayIndex = knownSpeakerDisplayIndex(speakerId, state.speakerOrder) || speakerIndex;
  if (gender) {
    const genderIndex = state.genderSpeakerOrder[gender].indexOf(speakerId) + 1;
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
}

function refreshConversationSpeakerLabels(
  state: SpeakerTranscriptionReducerState,
  lines: ConversationLineView[],
): ConversationLineView[] {
  return lines.map((line) => {
    const speakerIndex = state.speakerOrder.indexOf(line.speakerId) + 1;
    const label = speakerLabelForId(state, line.speakerId, speakerIndex || 1);
    return {
      ...line,
      speakerTitle: label.title,
      speakerBadgeClass: label.badgeClass,
      speakerToneClass: label.toneClass,
    };
  });
}

function stateWithSpeakerEvent(
  state: SpeakerTranscriptionReducerState,
  speakerId: string,
  speakerGender?: string | null,
): {
  state: SpeakerTranscriptionReducerState;
  label: { title: string; badgeClass: string; toneClass: string };
  labelsChanged: boolean;
} {
  const speakerOrder = state.speakerOrder.includes(speakerId)
    ? state.speakerOrder
    : [...state.speakerOrder, speakerId];
  const normalizedGender = normalizeSpeakerGender(speakerGender);
  const previousGender = state.speakerGenderById[speakerId] || "";
  let speakerGenderById = state.speakerGenderById;
  let genderSpeakerOrder = state.genderSpeakerOrder;
  if (normalizedGender && !previousGender) {
    speakerGenderById = {
      ...speakerGenderById,
      [speakerId]: normalizedGender,
    };
    if (!genderSpeakerOrder[normalizedGender].includes(speakerId)) {
      genderSpeakerOrder = {
        ...genderSpeakerOrder,
        [normalizedGender]: [...genderSpeakerOrder[normalizedGender], speakerId],
      };
    }
  }
  const nextState = {
    ...state,
    speakerOrder,
    speakerGenderById,
    genderSpeakerOrder,
  };
  const speakerIndex = speakerOrder.indexOf(speakerId) + 1;
  return {
    state: nextState,
    label: speakerLabelForId(nextState, speakerId, speakerIndex),
    labelsChanged: previousGender !== (speakerGenderById[speakerId] || ""),
  };
}

export function reduceSpeakerTranscriptionEvent(
  current: SpeakerTranscriptionReducerState,
  event: SpeakerTranscriptionGatewayEvent,
  options: {
    runSerial: number;
    nowMs?: number;
  },
): SpeakerTranscriptionReduceResult {
  const previousHasTranscript = speakerTranscriptionHasTranscript(current);
  if (event.type !== "speaker.partial" && event.type !== "speaker.final") {
    return {
      state: current,
      patch: null,
      hasTranscriptChanged: false,
      hasTranscript: previousHasTranscript,
      proofreadRequest: null,
    };
  }

  const speakerId = event.speaker_id || UNKNOWN_SPEAKER_ID;
  const speakerEvent = stateWithSpeakerEvent(current, speakerId, event.speaker_gender);
  let state = speakerEvent.state;
  const existing = state.streamStateBySpeakerId[speakerId] || speakerStreamState();
  let conversationLines = speakerEvent.labelsChanged
    ? refreshConversationSpeakerLabels(state, state.conversationLines)
    : state.conversationLines;
  let conversationLinesChanged = speakerEvent.labelsChanged;
  let activePartial = state.activePartial;
  let conversationScrollTarget = "";
  const text = event.text || "";
  let partialText = existing.partialText;
  let partialScrollTop = existing.partialScrollTop;
  let cumulativeFinalText = existing.cumulativeFinalText;
  let cumulativeFinalStartMs = existing.cumulativeFinalStartMs;
  let cumulativeFinalEndMs = existing.cumulativeFinalEndMs;
  let proofreadRequest: SpeakerTranscriptionProofreadRequest | null = null;
  let latestFinalLineId = state.latestFinalLineId;

  if (event.type === "speaker.final") {
    state = { ...state, lastPartialSignature: "" };
    const lineId = `run-${options.runSerial}-${stableLineIdForEvent(
      speakerId,
      event,
      `${speakerId}-${options.nowMs ?? Date.now()}-${conversationLines.length}`,
    )}`;
    const line = finalLineFromSpeakerEvent(event, existing, lineId);
    if (line) {
      let finalLineChanged = false;
      const existingConversationLine = conversationLines.find((item) => item.id === line.id);
      if (
        existingConversationLine
        && existingConversationLine.text === line.text
        && existingConversationLine.metaText === line.metaText
      ) {
        proofreadRequest = null;
      } else if (existingConversationLine) {
        const textChanged = existingConversationLine.text !== line.text;
        const contextLines = textChanged
          ? speakerProofreadContextLines(
            conversationLines.filter((item) => item.id !== line.id),
          )
          : [];
        const linePatch = {
          ...line,
          proofreadLoading: textChanged
            ? true
            : existingConversationLine.proofreadLoading,
          proofreadStatusText: textChanged
            ? "校对中"
            : existingConversationLine.proofreadStatusText,
        };
        conversationLines = patchConversationLine(conversationLines, line.id, linePatch);
        if (textChanged) {
          proofreadRequest = {
            lineId: line.id,
            text: line.text,
            contextLines,
          };
        }
        finalLineChanged = true;
      } else {
        const contextLines = speakerProofreadContextLines(conversationLines);
        line.proofreadLoading = true;
        line.proofreadStatusText = "校对中";
        proofreadRequest = {
          lineId: line.id,
          text: line.text,
          contextLines,
        };
        conversationLines = [
          conversationLineFromSpeakerLine(
            speakerId,
            speakerEvent.label.title,
            speakerEvent.label.badgeClass,
            speakerEvent.label.toneClass,
            line,
          ),
          ...conversationLines,
        ];
        finalLineChanged = true;
      }
      if (finalLineChanged) {
        latestFinalLineId = line.id;
        conversationScrollTarget = lineDomId(line.id);
        conversationLinesChanged = true;
      }
    }
    partialText = "";
    partialScrollTop = 0;
    activePartial = activePartialAfterConversationLines(state.activePartial, conversationLines);
    if (activePartial.visible) {
      conversationScrollTarget = "active-partial-card";
    }
    if (text.trim()) {
      cumulativeFinalText = text.trim();
      cumulativeFinalStartMs = eventStartMs(event);
      cumulativeFinalEndMs = eventEndMs(event);
    }
  } else {
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
    if (partialIsLong) {
      if (partialText !== existing.partialText) {
        partialScrollTop += 10000;
      }
    } else {
      partialScrollTop = 0;
    }
    activePartial = partialText
      ? {
        visible: true,
        speakerId,
        speakerTitle: speakerEvent.label.title,
        speakerBadgeClass: speakerEvent.label.badgeClass,
        speakerToneClass: speakerEvent.label.toneClass,
        partialBoxClass: partialBoxClass(speakerEvent.label.toneClass),
        text: partialText,
        startMs: eventStart,
        endMs: eventEndMs(event),
        partialScrollTop,
      }
      : EMPTY_ACTIVE_PARTIAL;
    conversationScrollTarget = partialText ? "active-partial-card" : "";
    const partialSignature = [
      speakerId,
      event.segment_id || "",
      partialText,
      String(partialScrollTop),
    ].join("|");
    if (!speakerEvent.labelsChanged && partialSignature === state.lastPartialSignature) {
      return {
        state,
        patch: null,
        hasTranscriptChanged: false,
        hasTranscript: previousHasTranscript,
        proofreadRequest: null,
      };
    }
    state = { ...state, lastPartialSignature: partialSignature };
  }

  state = {
    ...state,
    streamStateBySpeakerId: {
      ...state.streamStateBySpeakerId,
      [speakerId]: {
        partialText,
        partialScrollTop,
        cumulativeFinalText,
        cumulativeFinalStartMs,
        cumulativeFinalEndMs,
      },
    },
  };

  if (
    !conversationLinesChanged
    && activePartial === state.activePartial
    && !speakerEvent.labelsChanged
  ) {
    return {
      state,
      patch: null,
      hasTranscriptChanged: false,
      hasTranscript: previousHasTranscript,
      proofreadRequest,
    };
  }

  const visibleConversationLines = conversationLinesChanged
    ? refreshConversationSpeakerLabels(
      state,
      applyLatestConversationLineClasses(conversationLines, latestFinalLineId),
    )
    : state.conversationLines;
  state = {
    ...state,
    conversationLines: visibleConversationLines,
    activePartial,
    conversationScrollTarget,
    latestFinalLineId,
  };
  const hasTranscript = speakerTranscriptionHasTranscript(state);
  return {
    state,
    patch: {
      activePartial,
      conversationScrollTarget,
      latestFinalLineId,
      ...(conversationLinesChanged ? { conversationLines: visibleConversationLines } : {}),
    },
    hasTranscriptChanged: previousHasTranscript !== hasTranscript,
    hasTranscript,
    proofreadRequest,
  };
}

export function applySpeakerTranscriptionProofread(
  state: SpeakerTranscriptionReducerState,
  lineId: string,
  patch: Pick<ConversationLineView, "text" | "proofreadLoading" | "proofreadStatusText">
    | Pick<ConversationLineView, "proofreadLoading" | "proofreadStatusText">,
  reconcileActivePartial: boolean,
): {
  state: SpeakerTranscriptionReducerState;
  patch: Pick<SpeakerTranscriptionPageData, "conversationLines" | "activePartial">;
} {
  const conversationLines = refreshConversationSpeakerLabels(
    state,
    applyLatestConversationLineClasses(
      patchConversationLine(state.conversationLines, lineId, patch),
      state.latestFinalLineId,
    ),
  );
  const activePartial = reconcileActivePartial
    ? activePartialAfterConversationLines(state.activePartial, conversationLines)
    : state.activePartial;
  const nextState = {
    ...state,
    conversationLines,
    activePartial,
  };
  return {
    state: nextState,
    patch: {
      conversationLines,
      activePartial,
    },
  };
}
