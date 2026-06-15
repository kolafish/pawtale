import type {
  MemoirGuidanceMode,
  MemoirVoiceSessionDetail,
  VoiceChatErrorInfo,
  VoiceChatTranscriptLine,
  VoiceGatewayState,
  VoiceGatewayServerEvent,
} from "../../domain/voice-chat";
import {
  classifyVoiceClientConnectionError,
  MemoirStreamingAsrClient,
  type MemoirCaptureStats,
  type VoiceChatConnectionStatus,
} from "../../services/memoir-streaming-asr";
import { createMemoirVoiceFollowUp, getMemoirVoiceSession } from "../../services/memoir-voice-api";
import {
  readMemoirVoiceSessionSnapshot,
  transcriptLinesFromVoiceSessionDetail,
  writeMemoirVoiceSessionSnapshot,
} from "../../stores/memoir-cache";

interface VoiceChatData {
  voiceSessionId: string;
  guidanceMode: MemoirGuidanceMode;
  currentGuidanceModule: string;
  status: VoiceChatConnectionStatus;
  statusText: string;
  recording: boolean;
  connecting: boolean;
  error: string;
  liveText: string;
  latestTranscriptText: string;
  transcriptLines: VoiceChatTranscriptLine[];
  assistantPromptText: string;
  followUpLoading: boolean;
  followUpError: string;
  lastFollowUpSourceMessageId: string;
  modeLabel: string;
  primaryActionText: string;
  captureStateText: string;
  captureFrameCount: number;
  captureKilobytes: number;
  captureDurationText: string;
  liveCharCount: number;
  totalTranscriptCharCount: number;
  gatewayStateText: string;
  persistenceStateText: string;
  errorKindText: string;
}

let streamingClient: MemoirStreamingAsrClient | null = null;
let followUpRequestSerial = 0;

function modeLabel(mode: MemoirGuidanceMode): string {
  return mode === "guided_interview" ? "AI 引导" : "自由讲述";
}

function statusText(status: VoiceChatConnectionStatus): string {
  const labels: Record<VoiceChatConnectionStatus, string> = {
    idle: "未连接",
    connecting: "连接中",
    ready: "已连接",
    listening: "正在听",
    stopping: "收尾中",
    closed: "已结束",
    error: "异常",
  };
  return labels[status];
}

function gatewayStateText(state?: VoiceGatewayState): string {
  const labels: Record<VoiceGatewayState, string> = {
    ready: "识别就绪",
    listening: "正在收音",
    finishing: "识别收尾中",
    stopped: "识别已结束",
    failed: "连接异常",
  };
  return state ? labels[state] : "识别未同步";
}

function errorKindText(error?: VoiceChatErrorInfo): string {
  if (!error) {
    return "";
  }
  const labels: Record<VoiceChatErrorInfo["kind"], string> = {
    connection: "连接错误",
    recorder: "录音错误",
    gateway: "网关错误",
    asr: "识别错误",
    persistence: "保存错误",
    timeout: "超时",
    unknown: "未知错误",
  };
  return labels[error.kind];
}

function latestTranscriptText(lines: VoiceChatTranscriptLine[]): string {
  const userLines = lines.filter((line) => (line.role || "user") === "user");
  return userLines.length ? userLines[userLines.length - 1].text : "";
}

function latestAssistantText(lines: VoiceChatTranscriptLine[]): string {
  const assistantLines = lines.filter((line) => line.role === "assistant");
  return assistantLines.length ? assistantLines[assistantLines.length - 1].text : "";
}

function latestUserMessageId(lines: VoiceChatTranscriptLine[]): string {
  const userLines = lines.filter((line) => (line.role || "user") === "user");
  const latest = userLines[userLines.length - 1];
  return latest?.message_id || latest?.id || "";
}

function countTextChars(text: string): number {
  return Array.from(text.replace(/\s/g, "")).length;
}

function totalTranscriptChars(lines: VoiceChatTranscriptLine[]): number {
  return lines
    .filter((line) => (line.role || "user") === "user")
    .reduce((total, line) => total + countTextChars(line.text), 0);
}

function formatDuration(milliseconds: number): string {
  const totalSeconds = Math.max(0, Math.floor(milliseconds / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes.toString().padStart(2, "0")}:${seconds.toString().padStart(2, "0")}`;
}

function upsertTranscriptLine(
  lines: VoiceChatTranscriptLine[],
  nextLine: VoiceChatTranscriptLine,
): VoiceChatTranscriptLine[] {
  const matchIndex = lines.findIndex((line) => Boolean(
    (nextLine.message_id && line.message_id === nextLine.message_id)
    || (nextLine.utterance_id && line.utterance_id === nextLine.utterance_id),
  ));
  if (matchIndex < 0) {
    return [...lines, nextLine];
  }
  return lines.map((line, index) => (index === matchIndex ? nextLine : line));
}

Page<VoiceChatData, {
  onLoad(query?: Record<string, string | undefined>): Promise<void>;
  onUnload(): void;
  hydrateVoiceSessionSnapshot(): void;
  refreshVoiceSessionSnapshot(): Promise<void>;
  applyVoiceSessionDetail(detail: MemoirVoiceSessionDetail): void;
  persistCurrentTranscriptSnapshot(): void;
  maybeGenerateOpeningFollowUp(): void;
  generateFollowUp(regenerate?: boolean): Promise<void>;
  regenerateFollowUp(): Promise<void>;
  connectGateway(): Promise<void>;
  toggleRecording(): void;
  finishSession(): Promise<void>;
  handleStatus(status: VoiceChatConnectionStatus): void;
  handleCaptureStats(stats: MemoirCaptureStats): void;
  handleGatewayEvent(event: VoiceGatewayServerEvent): void;
  handlePartial(text: string): void;
  handleFinal(event: VoiceGatewayServerEvent): void;
  handleError(message: string, error?: VoiceChatErrorInfo): void;
}>({
  data: {
    voiceSessionId: "",
    guidanceMode: "free_narration",
    currentGuidanceModule: "",
    status: "idle",
    statusText: "未连接",
    recording: false,
    connecting: false,
    error: "",
    liveText: "",
    latestTranscriptText: "",
    transcriptLines: [],
    assistantPromptText: "",
    followUpLoading: false,
    followUpError: "",
    lastFollowUpSourceMessageId: "",
    modeLabel: "自由讲述",
    primaryActionText: "开始说话",
    captureStateText: "麦克风未开始",
    captureFrameCount: 0,
    captureKilobytes: 0,
    captureDurationText: "00:00",
    liveCharCount: 0,
    totalTranscriptCharCount: 0,
    gatewayStateText: "识别未同步",
    persistenceStateText: "等待识别",
    errorKindText: "",
  },

  async onLoad(query) {
    const voiceSessionId = query?.voiceSessionId || "";
    const guidanceMode = query?.mode === "guided_interview" ? "guided_interview" : "free_narration";
    const currentGuidanceModule = query?.module || "";
    this.setData({
      voiceSessionId,
      guidanceMode,
      currentGuidanceModule,
      modeLabel: modeLabel(guidanceMode),
    });
    if (!voiceSessionId) {
      this.setData({ error: "缺少语音会话" });
      return;
    }
    this.hydrateVoiceSessionSnapshot();
    void this.refreshVoiceSessionSnapshot();
    await this.connectGateway();
  },

  onUnload() {
    followUpRequestSerial += 1;
    this.persistCurrentTranscriptSnapshot();
    streamingClient?.close();
    streamingClient = null;
  },

  hydrateVoiceSessionSnapshot() {
    const snapshot = readMemoirVoiceSessionSnapshot(this.data.voiceSessionId);
    if (!snapshot.hasSession) {
      return;
    }
    const guidanceMode = snapshot.detail?.session.guidance_mode || this.data.guidanceMode;
    const currentGuidanceModule = snapshot.detail?.session.current_guidance_module || this.data.currentGuidanceModule;
    this.setData({
      guidanceMode,
      currentGuidanceModule,
      modeLabel: modeLabel(guidanceMode),
      transcriptLines: snapshot.transcriptLines,
      latestTranscriptText: latestTranscriptText(snapshot.transcriptLines),
      assistantPromptText: latestAssistantText(snapshot.transcriptLines),
      totalTranscriptCharCount: totalTranscriptChars(snapshot.transcriptLines),
    });
  },

  async refreshVoiceSessionSnapshot() {
    if (!this.data.voiceSessionId) {
      return;
    }
    try {
      const detail = await getMemoirVoiceSession(this.data.voiceSessionId);
      writeMemoirVoiceSessionSnapshot(this.data.voiceSessionId, { detail });
      this.applyVoiceSessionDetail(detail);
      this.maybeGenerateOpeningFollowUp();
    } catch (error) {
      console.warn("refresh voice session snapshot failed", error);
    }
  },

  applyVoiceSessionDetail(detail) {
    const detailLines = transcriptLinesFromVoiceSessionDetail(detail);
    const guidanceMode = detail.session.guidance_mode || this.data.guidanceMode;
    const currentGuidanceModule = detail.session.current_guidance_module || this.data.currentGuidanceModule;
    const nextLines = detailLines.length >= this.data.transcriptLines.length
      ? detailLines
      : this.data.transcriptLines;
    this.setData({
      guidanceMode,
      currentGuidanceModule,
      modeLabel: modeLabel(guidanceMode),
      transcriptLines: nextLines,
      latestTranscriptText: latestTranscriptText(nextLines),
      assistantPromptText: latestAssistantText(nextLines),
      totalTranscriptCharCount: totalTranscriptChars(nextLines),
    });
  },

  persistCurrentTranscriptSnapshot() {
    if (!this.data.voiceSessionId) {
      return;
    }
    writeMemoirVoiceSessionSnapshot(this.data.voiceSessionId, {
      transcriptLines: this.data.transcriptLines,
    });
  },

  maybeGenerateOpeningFollowUp() {
    if (this.data.guidanceMode !== "guided_interview" || this.data.followUpLoading) {
      return;
    }
    const hasAnyLine = this.data.transcriptLines.some((line) => line.text.trim());
    if (!hasAnyLine) {
      void this.generateFollowUp(false);
    }
  },

  async generateFollowUp(regenerate = false) {
    if (!this.data.voiceSessionId || this.data.followUpLoading) {
      return;
    }
    const sourceMessageId = latestUserMessageId(this.data.transcriptLines) || "opening";
    if (!regenerate && this.data.lastFollowUpSourceMessageId === sourceMessageId) {
      return;
    }
    const requestSerial = followUpRequestSerial + 1;
    followUpRequestSerial = requestSerial;
    this.setData({
      followUpLoading: true,
      followUpError: "",
      persistenceStateText: "AI 思考中",
    });
    try {
      const response = await createMemoirVoiceFollowUp(this.data.voiceSessionId, { regenerate });
      if (requestSerial !== followUpRequestSerial) {
        return;
      }
      const line: VoiceChatTranscriptLine = {
        id: response.message.id,
        role: response.message.role,
        text: response.message.content_text,
        message_id: response.message.id,
        transcript_version_id: response.message.transcript_version_id,
      };
      const transcriptLines = upsertTranscriptLine(this.data.transcriptLines, line);
      this.setData({
        transcriptLines,
        assistantPromptText: response.follow_up_text,
        totalTranscriptCharCount: totalTranscriptChars(transcriptLines),
        persistenceStateText: "等待回答",
        lastFollowUpSourceMessageId: sourceMessageId,
      });
      writeMemoirVoiceSessionSnapshot(this.data.voiceSessionId, { transcriptLines });
    } catch (error) {
      if (requestSerial !== followUpRequestSerial) {
        return;
      }
      this.setData({
        followUpError: error instanceof Error ? error.message : "AI 追问生成失败",
        persistenceStateText: "追问失败",
      });
    } finally {
      if (requestSerial === followUpRequestSerial) {
        this.setData({ followUpLoading: false });
      }
    }
  },

  async regenerateFollowUp() {
    await this.generateFollowUp(true);
  },

  async connectGateway() {
    if (this.data.connecting || !this.data.voiceSessionId) {
      return;
    }
    this.setData({ connecting: true, error: "" });
    streamingClient?.close({ force: true });
    streamingClient = new MemoirStreamingAsrClient({
      voiceSessionId: this.data.voiceSessionId,
      guidanceMode: this.data.guidanceMode,
      currentGuidanceModule: this.data.currentGuidanceModule,
      callbacks: {
        onStatus: (status) => this.handleStatus(status),
        onCaptureStats: (stats) => this.handleCaptureStats(stats),
        onEvent: (event) => this.handleGatewayEvent(event),
        onPartial: (text) => this.handlePartial(text),
        onFinal: (event) => this.handleFinal(event),
        onError: (message, error) => this.handleError(message, error),
      },
    });
    try {
      await streamingClient.connect();
    } catch (error) {
      const message = error instanceof Error ? error.message : "语音连接失败";
      this.handleError(message, classifyVoiceClientConnectionError(message));
    } finally {
      this.setData({ connecting: false });
    }
  },

  toggleRecording() {
    if (!streamingClient || this.data.status === "closed") {
      void this.connectGateway();
      return;
    }
    if (this.data.recording) {
      streamingClient.stopRecording();
      this.setData({ recording: false, primaryActionText: "继续说话" });
      return;
    }
    const started = streamingClient.startRecording();
    if (!started) {
      void this.connectGateway();
      return;
    }
    this.setData({
      recording: true,
      error: "",
      liveText: "",
      liveCharCount: 0,
      primaryActionText: "暂停收音",
    });
  },

  async finishSession() {
    if (!streamingClient) {
      await this.refreshVoiceSessionSnapshot();
      return;
    }
    const client = streamingClient;
    try {
      await client.finishSession();
      streamingClient = null;
      this.setData({
        recording: false,
        primaryActionText: "继续说话",
        status: "closed",
        statusText: statusText("closed"),
      });
      await this.refreshVoiceSessionSnapshot();
    } catch (error) {
      this.handleError(error instanceof Error ? error.message : "语音会话结束失败");
    }
  },

  handleStatus(status) {
    this.setData({
      status,
      statusText: statusText(status),
      recording: status === "listening" ? true : this.data.recording && status !== "closed" && status !== "error",
    });
  },

  handleCaptureStats(stats) {
    const labels: Record<MemoirCaptureStats["state"], string> = {
      idle: "麦克风未开始",
      opening: "正在打开麦克风",
      recording: "录音中",
      stopping: "正在收尾",
      error: "录音异常",
    };
    this.setData({
      captureStateText: labels[stats.state],
      captureFrameCount: stats.frameCount,
      captureKilobytes: Math.round(stats.audioBytesSent / 1024),
      captureDurationText: formatDuration(stats.recordingElapsedMs),
      gatewayStateText: gatewayStateText(stats.gatewayState),
    });
  },

  handleGatewayEvent(event) {
    const nextData: Partial<VoiceChatData> = {};
    if (event.state) {
      nextData.gatewayStateText = gatewayStateText(event.state);
    }
    if (event.type === "audio.ended") {
      nextData.recording = false;
      nextData.primaryActionText = "继续说话";
      nextData.persistenceStateText = this.data.persistenceStateText === "已保存" ? "已保存" : "正在保存";
    }
    if (event.type === "session.started") {
      nextData.persistenceStateText = "等待识别";
    }
    if (Object.keys(nextData).length) {
      this.setData(nextData);
    }
  },

  handlePartial(text) {
    this.setData({
      liveText: text,
      liveCharCount: countTextChars(text),
      persistenceStateText: "识别中",
      error: "",
      errorKindText: "",
    });
  },

  handleFinal(event) {
    const text = event.text || "";
    if (!text) {
      return;
    }
    const line: VoiceChatTranscriptLine = {
      id: event.message_id || event.utterance_id || `${Date.now()}-${this.data.transcriptLines.length}`,
      text,
      role: "user",
      message_id: event.message_id,
      utterance_id: event.utterance_id,
      audio_file_id: event.audio_file_id,
      transcript_version_id: event.transcript_version_id,
      start_ms: event.start_ms,
      end_ms: event.end_ms,
    };
    const transcriptLines = upsertTranscriptLine(this.data.transcriptLines, line);
    this.setData({
      liveText: "",
      liveCharCount: 0,
      latestTranscriptText: latestTranscriptText(transcriptLines),
      transcriptLines,
      totalTranscriptCharCount: totalTranscriptChars(transcriptLines),
      persistenceStateText: event.message_id && event.utterance_id && event.transcript_version_id
        ? "已保存"
        : "待同步",
      error: "",
      errorKindText: "",
    });
    writeMemoirVoiceSessionSnapshot(this.data.voiceSessionId, { transcriptLines });
    if (event.message_id) {
      void this.generateFollowUp(false);
    }
  },

  handleError(message, error) {
    const nextData: Partial<VoiceChatData> = {
      error: message,
      errorKindText: errorKindText(error),
      persistenceStateText: error?.kind === "persistence" ? "保存失败" : this.data.persistenceStateText,
      status: "error",
      statusText: statusText("error"),
      recording: false,
      primaryActionText: "重新连接",
      captureStateText: "录音异常",
    };
    if (error?.state) {
      nextData.gatewayStateText = gatewayStateText(error.state);
    }
    this.setData(nextData);
  },
});
