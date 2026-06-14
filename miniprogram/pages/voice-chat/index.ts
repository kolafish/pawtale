import type {
  MemoirGuidanceMode,
  VoiceChatTranscriptLine,
  VoiceGatewayServerEvent,
} from "../../domain/voice-chat";
import {
  MemoirStreamingAsrClient,
  type VoiceChatConnectionStatus,
} from "../../services/memoir-streaming-asr";

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

Page<VoiceChatData, {
  onLoad(query?: Record<string, string | undefined>): Promise<void>;
  onUnload(): void;
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
    await this.connectGateway();
  },

  onUnload() {
    streamingClient?.close();
    streamingClient = null;
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
      id: `${Date.now()}-${this.data.transcriptLines.length}`,
      text,
      message_id: event.message_id,
      utterance_id: event.utterance_id,
      audio_file_id: event.audio_file_id,
      transcript_version_id: event.transcript_version_id,
      start_ms: event.start_ms,
      end_ms: event.end_ms,
    };
    this.setData({
      liveText: "",
      transcriptLines: [...this.data.transcriptLines, line],
      error: "",
    });
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
