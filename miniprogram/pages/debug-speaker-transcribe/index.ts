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
  metaText: string;
  startMs: number | null;
  endMs: number | null;
  proofreadStatusText: string;
  proofreadLoading: boolean;
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

interface DebugSpeakerPageData {
  pageClass: string;
  statusText: string;
  statusClass: string;
  primaryButtonText: string;
  primaryButtonClass: string;
  recording: boolean;
  busy: boolean;
  error: string;
  speakers: SpeakerWindowView[];
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
}

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

function debugSpeakerPageClass(largeTextEnabled = readAccessibilitySettings().largeTextEnabled): string {
  return largeTextEnabled ? "debug-speaker-page large-text" : "debug-speaker-page";
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

function lineClasses(lineId: string, latestLineId: string): Pick<SpeakerLineView, "lineClass" | "textClass"> {
  const stateClass = lineId === latestLineId ? "latest" : "history";
  return {
    lineClass: `line-item ${stateClass}`,
    textClass: `line-text ${stateClass}`,
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

function debugSpeakerProofreadContextLines(
  speakers: SpeakerWindowView[],
  limit = 6,
): DebugSpeakerProofreadContextLine[] {
  const lines = speakers.flatMap((speaker) => speaker.lines.map((line) => ({
    speakerId: speaker.speakerId,
    text: line.text.trim(),
    startMs: line.startMs,
    endMs: line.endMs,
    sortMs: line.endMs ?? line.startMs ?? 0,
  })));
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
  speakers: SpeakerWindowView[],
): Pick<
  DebugSpeakerPageData,
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
    statusText: statusText(status),
    statusClass: statusClass(status),
    primaryButtonText: recording ? "停止识别" : (busy ? statusText(status) : "开始识别"),
    primaryButtonClass: recording ? "primary-button stop" : "primary-button",
    recording,
    busy,
    showEmpty: speakers.length === 0,
    clearDisabled: busy || recording || speakers.length === 0,
  };
}

Page<DebugSpeakerPageData, {
  client: DebugSpeakerTranscribeClient | null;
  speakerOrder: string[];
  onLoad(): void;
  onShow(): void;
  onUnload(): void;
  refreshAccessibilitySettings(): void;
  setStatus(status: DebugSpeakerConnectionStatus): void;
  handleSpeakerEvent(event: DebugSpeakerGatewayEvent): void;
  handleDiarizationStatus(event: DebugSpeakerGatewayEvent): void;
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
  speakerOrder: [],

  data: {
    pageClass: "debug-speaker-page",
    statusText: "未开始",
    statusClass: "status-pill",
    primaryButtonText: "开始识别",
    primaryButtonClass: "primary-button",
    recording: false,
    busy: false,
    error: "",
    speakers: [],
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
  },

  onLoad() {
    this.refreshAccessibilitySettings();
  },

  onShow() {
    this.refreshAccessibilitySettings();
  },

  onUnload() {
    this.client?.close();
    this.client = null;
  },

  refreshAccessibilitySettings() {
    this.setData({ pageClass: debugSpeakerPageClass() });
  },

  setStatus(status) {
    this.setData(pagePatch(status, this.data.speakers));
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
    const speakerOrder = this.speakerOrder.includes(speakerId)
      ? this.speakerOrder
      : [...this.speakerOrder, speakerId];
    this.speakerOrder = speakerOrder;
    const speakerIndex = speakerOrder.indexOf(speakerId) + 1;
    const speakers = [...this.data.speakers];
    const existingIndex = speakers.findIndex((item) => item.speakerId === speakerId);
    const existing = existingIndex >= 0
      ? speakers[existingIndex]
      : speakerWindow(speakerId, speakerIndex);
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
        proofreadContextLines = debugSpeakerProofreadContextLines(this.data.speakers);
        line.proofreadLoading = true;
        line.proofreadStatusText = "校对中";
        nextLines.unshift(line);
        lineForProofread = line;
        latestFinalLineId = line.id;
      }
      partialText = "";
      partialScrollClass = "partial-scroll";
      partialScrollTarget = "";
      partialScrollTop = 0;
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
      partialScrollClass = partialText.length > 90 ? "partial-scroll long" : "partial-scroll";
      partialScrollTop += 10000;
      partialEndId = `${speakerDomId(speakerId)}-partial-end-${partialScrollTop}`;
      partialScrollTarget = partialEndId;
    }
    const nextSpeaker = speakerWindow(speakerId, speakerIndex, {
      ...existing,
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
    this.setData({
      speakers: visibleSpeakers,
      latestFinalLineId,
      ...pagePatch(this.data.recording ? "listening" : "ready", visibleSpeakers),
    });
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
        ? "已收到说话人标签，会按不同人分窗口显示。"
        : "当前 ASR 返回结果没有说话人标签，暂时只能显示在一个窗口。"),
      diarizationNoticeClass: available ? "diarization-notice ok" : "diarization-notice warning",
    });
  },

  async proofreadFinalLine(speakerId, lineId, text, contextLines) {
    try {
      const result = await proofreadDebugSpeakerTranscriptLine(text, contextLines);
      const proofreadText = result.proofread_text.trim();
      const speakers = applyLatestLineClasses(
        patchSpeakerLine(this.data.speakers, speakerId, lineId, {
          text: proofreadText || text,
          proofreadLoading: false,
          proofreadStatusText: "已校对",
        }),
        this.data.latestFinalLineId,
      );
      this.setData({ speakers });
    } catch (_error) {
      const speakers = applyLatestLineClasses(
        patchSpeakerLine(this.data.speakers, speakerId, lineId, {
          proofreadLoading: false,
          proofreadStatusText: "校对失败",
        }),
        this.data.latestFinalLineId,
      );
      this.setData({ speakers });
    }
  },

  clearTranscript() {
    if (this.data.recording || this.data.busy) {
      return;
    }
    this.speakerOrder = [];
    const speakers: SpeakerWindowView[] = [];
    this.setData({
      speakers,
      elapsedText: "0:00",
      frameCountText: "0 帧",
      audioBytesText: "0 B",
      audioLevelStyle: "width: 0%;",
      showDiarizationNotice: false,
      diarizationNotice: "",
      diarizationNoticeClass: "diarization-notice",
      latestFinalLineId: "",
      ...pagePatch("idle", speakers),
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
