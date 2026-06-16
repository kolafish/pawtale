import type {
  MemoirGuidanceMode,
  MemoirVoiceSessionDetail,
  VoiceChatErrorInfo,
  VoiceChatTranscriptLine,
  VoiceGatewayState,
  VoiceGatewayServerEvent,
} from "../../domain/voice-chat";
import { configureAudiblePlayback } from "../../services/audio-playback";
import {
  classifyVoiceClientConnectionError,
  MemoirStreamingAsrClient,
  type MemoirCaptureStats,
  type VoiceChatConnectionStatus,
} from "../../services/memoir-streaming-asr";
import {
  createMemoirAssistantTTS,
  createMemoirVoiceFollowUp,
  downloadMemoirVoiceAudio,
  getMemoirVoiceSession,
} from "../../services/memoir-voice-api";
import { splitTextByPlaybackProgress } from "../../services/tts-text-progress";
import { canAutoStartListeningAfterTTS } from "../../services/voice-chat-auto-listen";
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
  assistantPromptLabel: string;
  assistantLoadingText: string;
  assistantEmptyText: string;
  followUpLoading: boolean;
  followUpError: string;
  lastFollowUpSourceMessageId: string;
  dismissedFollowUpMessageId: string;
  canRequestFollowUp: boolean;
  followUpMessageId: string;
  followUpAudioFileId: string;
  followUpTTSLoading: boolean;
  followUpTTSPlaying: boolean;
  followUpTTSError: string;
  followUpTTSStateText: string;
  ttsSpokenText: string;
  ttsRemainingText: string;
  ttsProgressPercent: number;
  modeLabel: string;
  primaryActionText: string;
  captureStateText: string;
  captureFrameCount: number;
  captureKilobytes: number;
  captureDurationText: string;
  audioLevelPercent: number;
  autoStopEnabled: boolean;
  autoStopStateText: string;
  autoListenEnabled: boolean;
  autoListenStateText: string;
  liveCharCount: number;
  totalTranscriptCharCount: number;
  gatewayStateText: string;
  persistenceStateText: string;
  errorKindText: string;
}

let streamingClient: MemoirStreamingAsrClient | null = null;
let followUpRequestSerial = 0;
const AUTO_STOP_SILENCE_MS = 1800;
const AUTO_STOP_MIN_RECORDING_MS = 1200;

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

function latestAssistantLine(lines: VoiceChatTranscriptLine[]): VoiceChatTranscriptLine | undefined {
  const latest = lines[lines.length - 1];
  return latest?.role === "assistant" ? latest : undefined;
}

function latestUserMessageId(lines: VoiceChatTranscriptLine[]): string {
  const userLines = lines.filter((line) => (line.role || "user") === "user");
  const latest = userLines[userLines.length - 1];
  return latest?.message_id || latest?.id || "";
}

function followUpSourceMessageId(lines: VoiceChatTranscriptLine[]): string {
  return latestUserMessageId(lines) || "opening";
}

function coveredFollowUpSourceMessageId(lines: VoiceChatTranscriptLine[]): string {
  const latest = lines[lines.length - 1];
  return latest?.role === "assistant" ? followUpSourceMessageId(lines) : "";
}

function latestAssistantMessageId(lines: VoiceChatTranscriptLine[]): string {
  const latest = latestAssistantLine(lines);
  return latest ? latest.message_id || latest.id : "";
}

function visibleAssistantLine(
  lines: VoiceChatTranscriptLine[],
  dismissedMessageId: string,
): VoiceChatTranscriptLine | undefined {
  const latest = latestAssistantLine(lines);
  if (!latest) {
    return undefined;
  }
  const latestMessageId = latest.message_id || latest.id;
  return latestMessageId === dismissedMessageId ? undefined : latest;
}

function canRequestFollowUp(
  lines: VoiceChatTranscriptLine[],
  mode: MemoirGuidanceMode,
  lastFollowUpSourceMessageId: string,
  followUpLoading: boolean,
): boolean {
  if (mode !== "guided_interview" || followUpLoading) {
    return false;
  }
  return followUpSourceMessageId(lines) !== lastFollowUpSourceMessageId;
}

function assistantPanelCopy(lines: VoiceChatTranscriptLine[]): {
  assistantPromptLabel: string;
  assistantLoadingText: string;
  assistantEmptyText: string;
} {
  const hasUserMessage = Boolean(latestUserMessageId(lines));
  return hasUserMessage
    ? {
        assistantPromptLabel: "AI 追问",
        assistantLoadingText: "正在根据刚才的内容生成下一句追问",
        assistantEmptyText: "可以让 AI 根据刚才的内容继续追问。",
      }
    : {
        assistantPromptLabel: "AI 开场",
        assistantLoadingText: "正在准备第一句话",
        assistantEmptyText: "AI 会先说一句，帮你开始讲。",
      };
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
  skipFollowUp(): void;
  prepareFollowUpTTS(
    messageId: string,
    forceRegenerate?: boolean,
    autoListenAfterPlayback?: boolean,
  ): Promise<void>;
  playFollowUpAudio(): Promise<void>;
  playAudioFile(audioFileId: string, options?: { autoListenAfterPlayback?: boolean }): Promise<void>;
  stopFollowUpAudio(): void;
  cancelFollowUpTTS(): void;
  updateFollowUpTTSProgress(currentTime: number, duration: number): void;
  resetFollowUpTTSProgress(): void;
  toggleAutoStop(): void;
  connectGateway(): Promise<void>;
  startRecording(autoTriggered?: boolean): boolean;
  toggleRecording(): void;
  shouldAutoStartListening(): boolean;
  finishSession(): Promise<void>;
  handleStatus(status: VoiceChatConnectionStatus): void;
  handleCaptureStats(stats: MemoirCaptureStats): void;
  handleGatewayEvent(event: VoiceGatewayServerEvent): void;
  handlePartial(text: string): void;
  handleFinal(event: VoiceGatewayServerEvent): void;
  handleError(message: string, error?: VoiceChatErrorInfo): void;
  audio: WechatMiniprogram.InnerAudioContext | null;
  ttsPlayToken: number;
  ttsRequestSerial: number;
}>({
  audio: null,
  ttsPlayToken: 0,
  ttsRequestSerial: 0,

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
    assistantPromptLabel: "AI 开场",
    assistantLoadingText: "正在准备第一句话",
    assistantEmptyText: "AI 会先说一句，帮你开始讲。",
    followUpLoading: false,
    followUpError: "",
    lastFollowUpSourceMessageId: "",
    dismissedFollowUpMessageId: "",
    canRequestFollowUp: false,
    followUpMessageId: "",
    followUpAudioFileId: "",
    followUpTTSLoading: false,
    followUpTTSPlaying: false,
    followUpTTSError: "",
    followUpTTSStateText: "",
    ttsSpokenText: "",
    ttsRemainingText: "",
    ttsProgressPercent: 0,
    modeLabel: "自由讲述",
    primaryActionText: "开始说话",
    captureStateText: "麦克风未开始",
    captureFrameCount: 0,
    captureKilobytes: 0,
    captureDurationText: "00:00",
    audioLevelPercent: 0,
    autoStopEnabled: true,
    autoStopStateText: "自动收尾开",
    autoListenEnabled: true,
    autoListenStateText: "AI 说完自动听",
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
    this.setData({ connecting: true });
    await this.refreshVoiceSessionSnapshot();
    this.setData({ connecting: false });
    await this.connectGateway();
  },

  onUnload() {
    followUpRequestSerial += 1;
    this.persistCurrentTranscriptSnapshot();
    this.cancelFollowUpTTS();
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
    const assistantLine = visibleAssistantLine(snapshot.transcriptLines, this.data.dismissedFollowUpMessageId);
    const coveredSourceMessageId = coveredFollowUpSourceMessageId(snapshot.transcriptLines);
    const lastFollowUpSourceMessageId = coveredSourceMessageId || this.data.lastFollowUpSourceMessageId;
    const assistantCopy = assistantPanelCopy(snapshot.transcriptLines);
    this.setData({
      guidanceMode,
      currentGuidanceModule,
      modeLabel: modeLabel(guidanceMode),
      transcriptLines: snapshot.transcriptLines,
      latestTranscriptText: latestTranscriptText(snapshot.transcriptLines),
      assistantPromptText: assistantLine?.text || "",
      ...assistantCopy,
      followUpMessageId: assistantLine?.message_id || assistantLine?.id || "",
      followUpAudioFileId: assistantLine?.audio_file_id || "",
      followUpTTSStateText: assistantLine?.audio_file_id ? "语音已生成" : this.data.followUpTTSStateText,
      totalTranscriptCharCount: totalTranscriptChars(snapshot.transcriptLines),
      lastFollowUpSourceMessageId,
      canRequestFollowUp: canRequestFollowUp(
        snapshot.transcriptLines,
        guidanceMode,
        lastFollowUpSourceMessageId,
        this.data.followUpLoading,
      ),
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
      if (detail.session.status !== "stopped" && detail.session.status !== "failed") {
        this.maybeGenerateOpeningFollowUp();
      }
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
    const coveredSourceMessageId = coveredFollowUpSourceMessageId(nextLines);
    const lastFollowUpSourceMessageId = coveredSourceMessageId || this.data.lastFollowUpSourceMessageId;
    const assistantLine = visibleAssistantLine(nextLines, this.data.dismissedFollowUpMessageId);
    const assistantCopy = assistantPanelCopy(nextLines);
    this.setData({
      guidanceMode,
      currentGuidanceModule,
      modeLabel: modeLabel(guidanceMode),
      transcriptLines: nextLines,
      latestTranscriptText: latestTranscriptText(nextLines),
      assistantPromptText: assistantLine?.text || "",
      ...assistantCopy,
      followUpMessageId: assistantLine?.message_id || assistantLine?.id || "",
      followUpAudioFileId: assistantLine?.audio_file_id || "",
      followUpTTSStateText: assistantLine?.audio_file_id ? "语音已生成" : this.data.followUpTTSStateText,
      totalTranscriptCharCount: totalTranscriptChars(nextLines),
      lastFollowUpSourceMessageId,
      canRequestFollowUp: canRequestFollowUp(
        nextLines,
        guidanceMode,
        lastFollowUpSourceMessageId,
        this.data.followUpLoading,
      ),
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
    if (this.data.followUpLoading) {
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
    const sourceMessageId = followUpSourceMessageId(this.data.transcriptLines);
    if (!regenerate && this.data.lastFollowUpSourceMessageId === sourceMessageId) {
      return;
    }
    const requestSerial = followUpRequestSerial + 1;
    followUpRequestSerial = requestSerial;
    this.setData({
      followUpLoading: true,
      followUpError: "",
      canRequestFollowUp: false,
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
      const assistantCopy = assistantPanelCopy(transcriptLines);
      this.setData({
        transcriptLines,
        assistantPromptText: response.follow_up_text,
        ...assistantCopy,
        dismissedFollowUpMessageId: "",
        followUpMessageId: response.message.id,
        followUpAudioFileId: response.message.audio_file_id || "",
        followUpTTSError: "",
        followUpTTSStateText: "",
        totalTranscriptCharCount: totalTranscriptChars(transcriptLines),
        persistenceStateText: "等待回答",
        lastFollowUpSourceMessageId: sourceMessageId,
        canRequestFollowUp: false,
      });
      writeMemoirVoiceSessionSnapshot(this.data.voiceSessionId, { transcriptLines });
      void this.prepareFollowUpTTS(response.message.id, false);
    } catch (error) {
      if (requestSerial !== followUpRequestSerial) {
        return;
      }
      this.setData({
        followUpError: error instanceof Error ? error.message : "AI 追问生成失败",
        canRequestFollowUp: true,
        persistenceStateText: "追问失败",
      });
    } finally {
      if (requestSerial === followUpRequestSerial) {
        this.setData({
          followUpLoading: false,
          canRequestFollowUp: canRequestFollowUp(
            this.data.transcriptLines,
            this.data.guidanceMode,
            this.data.lastFollowUpSourceMessageId,
            false,
          ),
        });
      }
    }
  },

  async regenerateFollowUp() {
    await this.generateFollowUp(true);
  },

  skipFollowUp() {
    const dismissedFollowUpMessageId = latestAssistantMessageId(this.data.transcriptLines);
    this.cancelFollowUpTTS();
    this.setData({
      assistantPromptText: "",
      followUpError: "",
      dismissedFollowUpMessageId,
      followUpMessageId: "",
      followUpAudioFileId: "",
      followUpTTSLoading: false,
      followUpTTSError: "",
      followUpTTSStateText: "",
      ttsSpokenText: "",
      ttsRemainingText: "",
      ttsProgressPercent: 0,
      canRequestFollowUp: canRequestFollowUp(
        this.data.transcriptLines,
        this.data.guidanceMode,
        this.data.lastFollowUpSourceMessageId,
        this.data.followUpLoading,
      ),
      persistenceStateText: "已跳过追问",
    });
  },

  async prepareFollowUpTTS(messageId, forceRegenerate = false, autoListenAfterPlayback = true) {
    if (!this.data.voiceSessionId || !messageId || this.data.followUpTTSLoading) {
      return;
    }
    this.stopFollowUpAudio();
    const requestSerial = this.ttsRequestSerial + 1;
    this.ttsRequestSerial = requestSerial;
    this.setData({
      followUpTTSLoading: true,
      followUpTTSError: "",
      followUpTTSStateText: "正在合成语音",
      ttsSpokenText: "",
      ttsRemainingText: this.data.assistantPromptText,
      ttsProgressPercent: 0,
    });
    try {
      const response = await createMemoirAssistantTTS(this.data.voiceSessionId, messageId, {
        force_regenerate: forceRegenerate,
      });
      if (requestSerial !== this.ttsRequestSerial || this.data.followUpMessageId !== response.message.id) {
        return;
      }
      const transcriptLines = this.data.transcriptLines.map((line) => {
        if ((line.message_id || line.id) !== response.message.id) {
          return line;
        }
        return {
          ...line,
          audio_file_id: response.audio_file_id,
        };
      });
      this.setData({
        transcriptLines,
        followUpMessageId: response.message.id,
        followUpAudioFileId: response.audio_file_id,
        followUpTTSError: "",
        followUpTTSStateText: "语音已生成",
      });
      writeMemoirVoiceSessionSnapshot(this.data.voiceSessionId, { transcriptLines });
      await this.playAudioFile(response.audio_file_id, { autoListenAfterPlayback });
    } catch (error) {
      if (requestSerial === this.ttsRequestSerial) {
        this.setData({
          followUpTTSError: error instanceof Error ? error.message : "AI 语音生成失败",
          followUpTTSStateText: "语音暂不可用",
        });
      }
    } finally {
      if (requestSerial === this.ttsRequestSerial) {
        this.setData({ followUpTTSLoading: false });
      }
    }
  },

  async playFollowUpAudio() {
    if (this.data.followUpTTSLoading) {
      return;
    }
    if (this.data.followUpAudioFileId) {
      await this.playAudioFile(this.data.followUpAudioFileId, { autoListenAfterPlayback: false });
      return;
    }
    if (this.data.followUpMessageId) {
      await this.prepareFollowUpTTS(this.data.followUpMessageId, false, false);
    }
  },

  async playAudioFile(audioFileId, options = {}) {
    if (!audioFileId) {
      return;
    }
    this.stopFollowUpAudio();
    const playToken = this.ttsPlayToken + 1;
    this.ttsPlayToken = playToken;
    this.setData({
      followUpTTSPlaying: true,
      followUpTTSError: "",
      followUpTTSStateText: "正在播放",
    });
    this.updateFollowUpTTSProgress(0, 0);
    try {
      const tempFilePath = await downloadMemoirVoiceAudio(audioFileId);
      if (playToken !== this.ttsPlayToken) {
        return;
      }
      const audio = wx.createInnerAudioContext();
      this.audio = audio;
      configureAudiblePlayback(audio);
      audio.src = tempFilePath;
      audio.onTimeUpdate(() => {
        if (playToken === this.ttsPlayToken) {
          this.updateFollowUpTTSProgress(audio.currentTime, audio.duration);
        }
      });
      audio.onEnded(() => {
        if (playToken === this.ttsPlayToken) {
          this.setData({
            followUpTTSPlaying: false,
            followUpTTSStateText: options.autoListenAfterPlayback ? "播放完成，正在听你说" : "播放完成",
            ttsSpokenText: this.data.assistantPromptText,
            ttsRemainingText: "",
            ttsProgressPercent: 100,
          });
        }
        audio.destroy();
        if (this.audio === audio) {
          this.audio = null;
        }
        if (playToken === this.ttsPlayToken && options.autoListenAfterPlayback && this.shouldAutoStartListening()) {
          this.startRecording(true);
        }
      });
      audio.onError(() => {
        if (playToken === this.ttsPlayToken) {
          this.setData({
            followUpTTSPlaying: false,
            followUpTTSError: "AI 语音播放失败",
            followUpTTSStateText: "播放失败",
          });
        }
        audio.destroy();
        if (this.audio === audio) {
          this.audio = null;
        }
      });
      audio.play();
    } catch (error) {
      if (playToken === this.ttsPlayToken) {
        this.setData({
          followUpTTSPlaying: false,
          followUpTTSError: error instanceof Error ? error.message : "AI 语音播放失败",
          followUpTTSStateText: "播放失败",
        });
      }
    }
  },

  stopFollowUpAudio() {
    this.ttsPlayToken += 1;
    if (this.audio) {
      this.audio.stop();
      this.audio.destroy();
      this.audio = null;
    }
    if (this.data.followUpTTSPlaying) {
      this.setData({ followUpTTSPlaying: false });
    }
    this.resetFollowUpTTSProgress();
  },

  cancelFollowUpTTS() {
    this.ttsRequestSerial += 1;
    this.stopFollowUpAudio();
    if (this.data.followUpTTSLoading || this.data.followUpTTSPlaying) {
      this.setData({
        followUpTTSLoading: false,
        followUpTTSPlaying: false,
        followUpTTSStateText: this.data.followUpTTSStateText ? "语音已停止" : "",
      });
    }
  },

  updateFollowUpTTSProgress(currentTime, duration) {
    const text = this.data.assistantPromptText;
    if (!text) {
      this.resetFollowUpTTSProgress();
      return;
    }
    const progressRatio = duration > 0 ? currentTime / duration : 0;
    const progress = splitTextByPlaybackProgress(text, progressRatio);
    this.setData({
      ttsSpokenText: progress.spokenText,
      ttsRemainingText: progress.remainingText,
      ttsProgressPercent: progress.progressPercent,
    });
  },

  resetFollowUpTTSProgress() {
    this.setData({
      ttsSpokenText: "",
      ttsRemainingText: "",
      ttsProgressPercent: 0,
    });
  },

  toggleAutoStop() {
    const autoStopEnabled = !this.data.autoStopEnabled;
    streamingClient?.configureAutoStop(autoStopEnabled ? AUTO_STOP_SILENCE_MS : 0);
    this.setData({
      autoStopEnabled,
      autoStopStateText: autoStopEnabled ? "自动收尾开" : "自动收尾关",
    });
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
      autoStopSilenceMs: this.data.autoStopEnabled ? AUTO_STOP_SILENCE_MS : 0,
      minAutoStopRecordingMs: AUTO_STOP_MIN_RECORDING_MS,
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

  startRecording(autoTriggered = false) {
    if (!streamingClient || this.data.status === "closed") {
      void this.connectGateway();
      this.setData({ autoListenStateText: autoTriggered ? "正在重新连接语音" : this.data.autoListenStateText });
      return false;
    }
    const started = streamingClient.startRecording();
    if (!started) {
      void this.connectGateway();
      this.setData({ autoListenStateText: autoTriggered ? "自动收音未就绪" : this.data.autoListenStateText });
      return false;
    }
    this.cancelFollowUpTTS();
    this.setData({
      recording: true,
      error: "",
      liveText: "",
      liveCharCount: 0,
      primaryActionText: "暂停收音",
      autoListenStateText: autoTriggered ? "AI 说完，正在听你说" : "AI 说完自动听",
    });
    return true;
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
    this.startRecording(false);
  },

  shouldAutoStartListening() {
    return Boolean(streamingClient) && canAutoStartListeningAfterTTS({
      autoListenEnabled: this.data.autoListenEnabled,
      status: this.data.status,
      recording: this.data.recording,
      connecting: this.data.connecting,
      followUpLoading: this.data.followUpLoading,
    });
  },

  async finishSession() {
    this.cancelFollowUpTTS();
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
      wx.showModal({
        title: "访谈已结束",
        content: "可以到转写记录里查看和校正文稿。",
        confirmText: "查看记录",
        cancelText: "留在本页",
        success: (result) => {
          if (result.confirm) {
            wx.navigateTo({ url: "/pages/transcripts/index" });
          }
        },
      });
    } catch (error) {
      this.handleError(error instanceof Error ? error.message : "语音会话结束失败");
    }
  },

  handleStatus(status) {
    this.setData({
      status,
      statusText: statusText(status),
      recording: status === "listening"
        ? true
        : this.data.recording && status !== "stopping" && status !== "closed" && status !== "error",
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
      audioLevelPercent: Math.min(100, Math.round(Math.sqrt(stats.audioLevel || 0) * 100)),
      autoStopStateText: !this.data.autoStopEnabled
        ? "自动收尾关"
        : (stats.autoStopped ? "检测到停顿，已自动收尾" : `自动收尾开 · 静音 ${formatDuration(stats.silentElapsedMs || 0)}`),
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
    const assistantCopy = assistantPanelCopy(transcriptLines);
    this.setData({
      liveText: "",
      liveCharCount: 0,
      latestTranscriptText: latestTranscriptText(transcriptLines),
      transcriptLines,
      ...assistantCopy,
      totalTranscriptCharCount: totalTranscriptChars(transcriptLines),
      persistenceStateText: event.message_id && event.utterance_id && event.transcript_version_id
        ? "已保存"
        : "待同步",
      error: "",
      errorKindText: "",
      assistantPromptText: "",
      dismissedFollowUpMessageId: "",
      followUpMessageId: "",
      followUpAudioFileId: "",
      followUpTTSLoading: false,
      followUpTTSError: "",
      followUpTTSStateText: "",
      canRequestFollowUp: canRequestFollowUp(
        transcriptLines,
        this.data.guidanceMode,
        this.data.lastFollowUpSourceMessageId,
        this.data.followUpLoading,
      ),
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
