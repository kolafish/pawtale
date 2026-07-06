import type { DebugSpeakerConnectionStatus, DebugSpeakerGatewayEvent } from "../../services/debug-speaker-transcribe";
import { readAccessibilitySettings } from "../../services/accessibility-settings";
import {
  DebugSpeakerTranscribeClient,
  debugSpeakerTitle,
} from "../../services/debug-speaker-transcribe";
import {
  proofreadDebugSpeakerTranscriptLine,
  type DebugSpeakerProofreadContextLine,
} from "../../services/debug-speaker-proofread";

interface SpeakerLineView {
  id: string;
  text: string;
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
  domId: string;
}

interface SpeakerWindowView {
  speakerId: string;
  title: string;
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
  text: string;
  partialEndId: string;
  partialScrollClass: string;
  partialScrollTarget: string;
  partialScrollTop: number;
}

interface HistoryLinePreviewView {
  visible: boolean;
  speakerTitle: string;
  metaText: string;
  statusText: string;
  text: string;
}

interface DebugSpeakerPageData {
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

const HISTORY_PREVIEW_HOLD_MS = 160;
const HISTORY_PREVIEW_CANCEL_DISTANCE = 18;
const EMPTY_HISTORY_LINE_PREVIEW: HistoryLinePreviewView = {
  visible: false,
  speakerTitle: "",
  metaText: "",
  statusText: "",
  text: "",
};
const EMPTY_ACTIVE_PARTIAL: ActivePartialView = {
  visible: false,
  speakerId: "",
  speakerTitle: "",
  speakerBadgeClass: "speaker-badge",
  text: "",
  partialEndId: "active-partial-end",
  partialScrollClass: "partial-scroll",
  partialScrollTarget: "",
  partialScrollTop: 0,
};
const DEBUG_SPEAKER_STATS_RENDER_INTERVAL_MS = 800;

function statusText(status: DebugSpeakerConnectionStatus): string {
  const texts: Record<DebugSpeakerConnectionStatus, string> = {
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

function statusClass(status: DebugSpeakerConnectionStatus): string {
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

function formatSegmentMeta(event: DebugSpeakerGatewayEvent): string {
  const range = typeof event.start_ms === "number" && typeof event.end_ms === "number"
    ? `${formatElapsed(event.start_ms)}-${formatElapsed(event.end_ms)}`
    : "";
  const confidence = typeof event.confidence === "number"
    ? `置信度 ${Math.round(event.confidence * 100)}%`
    : "";
  return [range, confidence].filter(Boolean).join(" · ");
}

function speakerDomId(speakerId: string): string {
  return speakerId.replace(/[^a-zA-Z0-9_-]/g, "_");
}

function lineDomId(lineId: string): string {
  return `conversation-${speakerDomId(lineId)}`;
}

function debugSpeakerPageClass(largeTextEnabled = readAccessibilitySettings().largeTextEnabled): string {
  return largeTextEnabled ? "debug-speaker-page large-text" : "debug-speaker-page";
}

function debugSpeakerControlPanelClass(hasTranscript: boolean): string {
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

function speakerBadgeClass(gender: string): string {
  if (gender === "male") {
    return "speaker-badge male";
  }
  if (gender === "female") {
    return "speaker-badge female";
  }
  return "speaker-badge";
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

function eventStartMs(event: DebugSpeakerGatewayEvent): number | null {
  return typeof event.start_ms === "number" ? event.start_ms : null;
}

function eventEndMs(event: DebugSpeakerGatewayEvent): number | null {
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
  event: DebugSpeakerGatewayEvent,
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

function lineClasses(lineId: string, latestLineId: string): Pick<SpeakerLineView, "lineClass" | "textClass" | "previewEnabled"> {
  const stateClass = lineId === latestLineId ? "latest" : "history";
  return {
    lineClass: `line-item ${stateClass}`,
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
      ...lineClasses(line.id, latestLineId),
    })),
  }));
}

function conversationLineFromSpeakerLine(
  speakerId: string,
  speakerTitle: string,
  speakerClass: string,
  line: SpeakerLineView,
): ConversationLineView {
  return {
    ...line,
    speakerId,
    speakerTitle,
    speakerBadgeClass: speakerClass,
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
    ...lineClasses(line.id, latestLineId),
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
    metaText: line.metaText,
    statusText: line.proofreadStatusText,
    text: line.text,
  };
}

function debugSpeakerProofreadContextLines(
  conversationLines: ConversationLineView[],
  limit = 6,
): DebugSpeakerProofreadContextLine[] {
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
    title: debugSpeakerTitle(speakerId, speakerIndex),
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
  status: DebugSpeakerConnectionStatus,
  hasTranscript: boolean,
): Pick<
  DebugSpeakerPageData,
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
    controlPanelClass: debugSpeakerControlPanelClass(hasTranscript),
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

Page<DebugSpeakerPageData, {
  client: DebugSpeakerTranscribeClient | null;
  lastStatsRenderMs: number;
  speakerOrder: string[];
  speakerGenderById: Record<string, "male" | "female" | "">;
  genderSpeakerOrder: Record<"male" | "female", string[]>;
  historyPreviewTimer: number | null;
  historyPreviewTouch: HistoryLineTouch | null;
  onLoad(): void;
  onShow(): void;
  onUnload(): void;
  refreshAccessibilitySettings(): void;
  setStatus(status: DebugSpeakerConnectionStatus): void;
  hasTranscript(): boolean;
  shouldRenderStats(nowMs: number): boolean;
  speakerLabelForEvent(speakerId: string, speakerIndex: number, speakerGender?: string | null): {
    title: string;
    badgeClass: string;
  };
  speakerLabelForId(speakerId: string, speakerIndex: number): {
    title: string;
    badgeClass: string;
  };
  refreshConversationSpeakerLabels(lines: ConversationLineView[]): ConversationLineView[];
  handleSpeakerEvent(event: DebugSpeakerGatewayEvent): void;
  handleDiarizationStatus(event: DebugSpeakerGatewayEvent): void;
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
    contextLines: DebugSpeakerProofreadContextLine[],
  ): Promise<void>;
  toggleRecording(): Promise<void>;
  clearTranscript(): void;
  goBack(): void;
}>({
  client: null,
  lastStatsRenderMs: 0,
  speakerOrder: [],
  speakerGenderById: {},
  genderSpeakerOrder: {
    male: [],
    female: [],
  },
  historyPreviewTimer: null as number | null,
  historyPreviewTouch: null as HistoryLineTouch | null,

  data: {
    pageClass: "debug-speaker-page",
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
    this.setData({ pageClass: debugSpeakerPageClass() });
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
    if (nowMs - this.lastStatsRenderMs < DEBUG_SPEAKER_STATS_RENDER_INTERVAL_MS) {
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
    if (gender) {
      const genderIndex = this.genderSpeakerOrder[gender].indexOf(speakerId) + 1;
      const title = genderSpeakerTitle(gender, genderIndex || 1);
      if (title) {
        return {
          title,
          badgeClass: speakerBadgeClass(gender),
        };
      }
    }
    return {
      title: debugSpeakerTitle(speakerId, speakerIndex),
      badgeClass: speakerBadgeClass(""),
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
    const client = new DebugSpeakerTranscribeClient({
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
    this.lastStatsRenderMs = 0;
    this.setData({
      error: "",
      showDiarizationNotice: false,
      diarizationNotice: "",
      diarizationNoticeClass: "diarization-notice",
    });
    try {
      await client.start();
    } catch (error) {
      this.setStatus("error");
      this.setData({ error: error instanceof Error ? error.message : "多人转写启动失败" });
    }
  },

  handleSpeakerEvent(event) {
    const speakerId = event.speaker_id || "speaker_unknown";
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
    let proofreadContextLines: DebugSpeakerProofreadContextLine[] = [];
    let latestFinalLineId = this.data.latestFinalLineId;
    if (event.type === "speaker.final") {
      const line = finalLineFromSpeakerEvent(event, existing, `${speakerId}-${Date.now()}-${nextLines.length}`);
      if (line) {
        proofreadContextLines = debugSpeakerProofreadContextLines(conversationLines);
        line.proofreadLoading = true;
        line.proofreadStatusText = "校对中";
        nextLines.unshift(line);
        lineForProofread = line;
        latestFinalLineId = line.id;
        const conversationLine = conversationLineFromSpeakerLine(
          speakerId,
          speakerLabel.title,
          speakerLabel.badgeClass,
          line,
        );
        conversationLines = [conversationLine, ...conversationLines];
        conversationLinesChanged = true;
      }
      partialText = "";
      partialScrollClass = "partial-scroll";
      partialScrollTarget = "";
      partialScrollTop = 0;
      activePartial = EMPTY_ACTIVE_PARTIAL;
      if (text.trim()) {
        cumulativeFinalText = text.trim();
        cumulativeFinalStartMs = eventStartMs(event);
        cumulativeFinalEndMs = eventEndMs(event);
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
      const partialIsLong = partialText.length > 90;
      partialScrollClass = partialIsLong ? "partial-scroll long" : "partial-scroll";
      if (partialIsLong) {
        partialScrollTop += 10000;
        partialEndId = `${speakerDomId(speakerId)}-partial-end-${partialScrollTop}`;
        partialScrollTarget = partialEndId;
      } else {
        partialScrollTarget = "";
      }
      activePartial = partialText
        ? {
          visible: true,
          speakerId,
          speakerTitle: speakerLabel.title,
          speakerBadgeClass: speakerLabel.badgeClass,
          text: partialText,
          partialEndId,
          partialScrollClass,
          partialScrollTarget,
          partialScrollTop,
        }
        : EMPTY_ACTIVE_PARTIAL;
    }
    const nextSpeaker = speakerWindow(speakerId, speakerIndex, {
      ...existing,
      title: speakerLabel.title,
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
    const dataPatch: Partial<DebugSpeakerPageData> = {
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
      const result = await proofreadDebugSpeakerTranscriptLine(text, contextLines);
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
      this.setData({ speakers, conversationLines });
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
  formatBytes as formatDebugSpeakerBytes,
  formatElapsed as formatDebugSpeakerElapsed,
  formatSegmentMeta as formatDebugSpeakerSegmentMeta,
  pagePatch as debugSpeakerPagePatch,
  debugSpeakerPageClass,
  debugSpeakerProofreadContextLines,
};
