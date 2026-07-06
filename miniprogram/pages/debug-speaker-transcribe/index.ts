import type { DebugSpeakerConnectionStatus, DebugSpeakerGatewayEvent } from "../../services/debug-speaker-transcribe";
import {
  DebugSpeakerTranscribeClient,
  debugSpeakerTitle,
} from "../../services/debug-speaker-transcribe";

interface SpeakerLineView {
  id: string;
  text: string;
  metaText: string;
}

interface SpeakerWindowView {
  speakerId: string;
  title: string;
  partialText: string;
  partialEndId: string;
  partialScrollClass: string;
  partialScrollTarget: string;
  partialScrollTop: number;
  lines: SpeakerLineView[];
  segmentCountText: string;
  cardClass: string;
  showNoLines: boolean;
}

interface DebugSpeakerPageData {
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
  onUnload(): void;
  setStatus(status: DebugSpeakerConnectionStatus): void;
  handleSpeakerEvent(event: DebugSpeakerGatewayEvent): void;
  toggleRecording(): Promise<void>;
  clearTranscript(): void;
  goBack(): void;
}>({
  client: null,
  speakerOrder: [],

  data: {
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
  },

  onUnload() {
    this.client?.close();
    this.client = null;
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
      onError: (message) => this.setData({ error: message }),
    });
    this.client = client;
    this.setData({ error: "" });
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
    if (event.type === "speaker.final") {
      if (text) {
        nextLines.unshift({
          id: event.segment_id || `${speakerId}-${Date.now()}-${nextLines.length}`,
          text,
          metaText: formatSegmentMeta(event),
        });
      }
      partialText = "";
      partialScrollClass = "partial-scroll";
      partialScrollTarget = "";
      partialScrollTop = 0;
    } else if (event.type === "speaker.partial") {
      partialText = text;
      partialScrollClass = text.length > 90 ? "partial-scroll long" : "partial-scroll";
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
    this.setData({
      speakers,
      ...pagePatch(this.data.recording ? "listening" : "ready", speakers),
    });
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
};
