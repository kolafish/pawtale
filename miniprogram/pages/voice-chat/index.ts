import type {
  MemoirGuidanceMode,
  MemoirVoiceSessionDetail,
  VoiceChatTranscriptLine,
  VoiceGatewayServerEvent,
} from "../../domain/voice-chat";
import {
  MemoirStreamingAsrClient,
  type VoiceChatConnectionStatus,
} from "../../services/memoir-streaming-asr";
import { getMemoirVoiceSession } from "../../services/memoir-voice-api";
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
  modeLabel: string;
  primaryActionText: string;
}

let streamingClient: MemoirStreamingAsrClient | null = null;

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

function latestTranscriptText(lines: VoiceChatTranscriptLine[]): string {
  return lines.length ? lines[lines.length - 1].text : "";
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
  connectGateway(): Promise<void>;
  toggleRecording(): void;
  finishSession(): Promise<void>;
  handleStatus(status: VoiceChatConnectionStatus): void;
  handleGatewayEvent(event: VoiceGatewayServerEvent): void;
  handlePartial(text: string): void;
  handleFinal(event: VoiceGatewayServerEvent): void;
  handleError(message: string): void;
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
    modeLabel: "自由讲述",
    primaryActionText: "开始说话",
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

  async connectGateway() {
    if (this.data.connecting || !this.data.voiceSessionId) {
      return;
    }
    this.setData({ connecting: true, error: "" });
    streamingClient?.close();
    streamingClient = new MemoirStreamingAsrClient({
      voiceSessionId: this.data.voiceSessionId,
      guidanceMode: this.data.guidanceMode,
      currentGuidanceModule: this.data.currentGuidanceModule,
      callbacks: {
        onStatus: (status) => this.handleStatus(status),
        onEvent: (event) => this.handleGatewayEvent(event),
        onPartial: (text) => this.handlePartial(text),
        onFinal: (event) => this.handleFinal(event),
        onError: (message) => this.handleError(message),
      },
    });
    try {
      await streamingClient.connect();
    } catch (error) {
      this.handleError(error instanceof Error ? error.message : "语音连接失败");
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
    streamingClient.startRecording();
    this.setData({
      recording: true,
      error: "",
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

  handleGatewayEvent(event) {
    if (event.type === "audio.ended") {
      this.setData({
        recording: false,
        primaryActionText: "继续说话",
      });
    }
  },

  handlePartial(text) {
    this.setData({
      liveText: text,
      error: "",
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
      latestTranscriptText: latestTranscriptText(transcriptLines),
      transcriptLines,
      error: "",
    });
    writeMemoirVoiceSessionSnapshot(this.data.voiceSessionId, { transcriptLines });
  },

  handleError(message) {
    this.setData({
      error: message,
      status: "error",
      statusText: statusText("error"),
      recording: false,
      primaryActionText: "重新连接",
    });
  },
});
