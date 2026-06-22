import type {
  MemoirGuidanceMode,
  MemoirVoiceSessionDetail,
  VoiceChatErrorInfo,
  TTSAlignmentSegment,
  VoiceChatTranscriptLine,
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
import { splitTextByAudioPlaybackProgress } from "../../services/tts-text-progress";
import { readChatTTSSettings } from "../../services/tts-settings";
import { canAutoStartListeningAfterTTS } from "../../services/voice-chat-auto-listen";
import { voiceGuidanceLabel } from "../../services/memoir-project-profile";
import {
  readVoiceAuthorizationConsent,
  saveVoiceAuthorizationConsent,
  VOICE_AUTHORIZATION_PAGE_URL,
} from "../../services/voice-authorization";
import { ensureRecorderPrivacyAuthorization } from "../../services/wechat-privacy";
import {
  assistantPanelCopy,
  canRequestFollowUp,
  countTextChars,
  coveredFollowUpSourceMessageId,
  errorKindText,
  followUpSourceMessageId,
  formatDuration,
  gatewayStateText,
  latestAssistantMessageId,
  latestTranscriptText,
  statusText,
  totalTranscriptChars,
  ttsAlignmentFromAssistantLine,
  ttsAlignmentFromMetadata,
  upsertTranscriptLine,
  visibleAssistantLine,
} from "../../services/voice-chat-state";
import {
  readMemoirVoiceSessionSnapshot,
  transcriptLinesFromVoiceSessionDetail,
  writeMemoirVoiceSessionSnapshot,
} from "../../stores/memoir-cache";

const VOICE_AUTHORIZATION_VALUE = "voice_authorization";

interface CheckboxChangeEvent {
  detail: {
    value: string[];
  };
}

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
  ttsCurrentText: string;
  ttsRemainingText: string;
  ttsProgressPercent: number;
  ttsAlignment: TTSAlignmentSegment[];
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
  voiceAuthorizationAgreed: boolean;
}

let streamingClient: MemoirStreamingAsrClient | null = null;
let followUpRequestSerial = 0;
const AUTO_STOP_SILENCE_MS = 1800;
const AUTO_STOP_MIN_RECORDING_MS = 1200;

Page<VoiceChatData, {
  onLoad(query?: Record<string, string | undefined>): Promise<void>;
  onShow(): void;
  onUnload(): void;
  refreshVoiceAuthorizationConsent(): void;
  changeVoiceAuthorization(event: CheckboxChangeEvent): void;
  openVoiceAuthorization(): void;
  ensureVoiceAuthorization(autoTriggered?: boolean): boolean;
  hydrateVoiceSessionSnapshot(): void;
  refreshVoiceSessionSnapshot(): Promise<void>;
  confirmSavedTranscriptSnapshot(event: VoiceGatewayServerEvent): Promise<void>;
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
  playbackTimeForProgress(audio: WechatMiniprogram.InnerAudioContext): number;
  startFollowUpProgressTimer(playToken: number, audio: WechatMiniprogram.InnerAudioContext): void;
  stopFollowUpProgressTimer(): void;
  resetFollowUpTTSProgress(): void;
  toggleAutoStop(): void;
  connectGateway(): Promise<void>;
  startRecording(autoTriggered?: boolean): boolean;
  startRecordingAfterPrivacy(autoTriggered?: boolean): Promise<boolean>;
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
  ttsProgressTimer: number | null;
  ttsProgressStartedAtMs: number;
  ttsRequestSerial: number;
}>({
  audio: null,
  ttsPlayToken: 0,
  ttsProgressTimer: null,
  ttsProgressStartedAtMs: 0,
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
    ttsCurrentText: "",
    ttsRemainingText: "",
    ttsProgressPercent: 0,
    ttsAlignment: [],
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
    voiceAuthorizationAgreed: false,
  },

  async onLoad(query) {
    const voiceSessionId = query?.voiceSessionId || "";
    const guidanceMode = query?.mode === "guided_interview" ? "guided_interview" : "free_narration";
    const currentGuidanceModule = query?.module || "";
    const voiceAuthorizationAgreed = readVoiceAuthorizationConsent();
    this.setData({
      voiceSessionId,
      guidanceMode,
      currentGuidanceModule,
      modeLabel: voiceGuidanceLabel(guidanceMode, currentGuidanceModule),
      voiceAuthorizationAgreed,
      primaryActionText: voiceAuthorizationAgreed ? "开始说话" : "同意后开始说话",
    });
    if (!voiceSessionId) {
      this.setData({ error: "缺少语音会话" });
      return;
    }
    this.hydrateVoiceSessionSnapshot();
    this.setData({ connecting: true });
    await this.refreshVoiceSessionSnapshot();
    this.setData({ connecting: false });
    if (this.data.voiceAuthorizationAgreed) {
      await this.connectGateway();
    } else {
      this.setData({
        statusText: "等待声纹授权",
        autoListenStateText: "同意后才会自动收音",
      });
    }
  },

  onShow() {
    this.refreshVoiceAuthorizationConsent();
  },

  onUnload() {
    followUpRequestSerial += 1;
    this.persistCurrentTranscriptSnapshot();
    this.cancelFollowUpTTS();
    streamingClient?.close();
    streamingClient = null;
  },

  refreshVoiceAuthorizationConsent() {
    const voiceAuthorizationAgreed = readVoiceAuthorizationConsent();
    const patch: Partial<VoiceChatData> = { voiceAuthorizationAgreed };
    if (voiceAuthorizationAgreed && this.data.primaryActionText === "同意后开始说话") {
      patch.primaryActionText = "开始说话";
      patch.error = "";
      patch.autoListenStateText = "AI 说完自动听";
    }
    if (!voiceAuthorizationAgreed && !this.data.recording) {
      patch.primaryActionText = "同意后开始说话";
      patch.autoListenStateText = "同意后才会自动收音";
    }
    this.setData(patch);
    if (
      voiceAuthorizationAgreed
      && this.data.voiceSessionId
      && !this.data.connecting
      && (!streamingClient || this.data.status === "closed")
    ) {
      void this.connectGateway();
    }
  },

  changeVoiceAuthorization(event) {
    const agreed = event.detail.value.includes(VOICE_AUTHORIZATION_VALUE);
    const consent = saveVoiceAuthorizationConsent(agreed);
    if (!agreed && streamingClient && this.data.recording) {
      streamingClient.stopRecording();
    }
    this.setData({
      voiceAuthorizationAgreed: consent.agreed,
      recording: agreed ? this.data.recording : false,
      error: agreed ? "" : this.data.error,
      primaryActionText: agreed ? "开始说话" : "同意后开始说话",
      autoListenStateText: agreed ? "AI 说完自动听" : "同意后才会自动收音",
    });
    if (agreed && this.data.voiceSessionId && !streamingClient && !this.data.connecting) {
      void this.connectGateway();
    }
  },

  openVoiceAuthorization() {
    wx.navigateTo({ url: VOICE_AUTHORIZATION_PAGE_URL });
  },

  ensureVoiceAuthorization(autoTriggered = false) {
    const latestConsent = readVoiceAuthorizationConsent();
    if (latestConsent) {
      if (!this.data.voiceAuthorizationAgreed) {
        this.setData({ voiceAuthorizationAgreed: true, error: "" });
      }
      return true;
    }
    this.setData({
      voiceAuthorizationAgreed: false,
      error: "请先阅读并同意《声纹授权协议》后再开始录音",
      primaryActionText: "同意后开始说话",
      autoListenStateText: autoTriggered ? "自动收音已暂停，等待声纹授权" : "同意后才会自动收音",
    });
    wx.showToast({ title: "请先同意声纹授权协议", icon: "none" });
    return false;
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
      modeLabel: voiceGuidanceLabel(guidanceMode, currentGuidanceModule),
      transcriptLines: snapshot.transcriptLines,
      latestTranscriptText: latestTranscriptText(snapshot.transcriptLines),
      assistantPromptText: assistantLine?.text || "",
      ...assistantCopy,
      followUpMessageId: assistantLine?.message_id || assistantLine?.id || "",
      followUpAudioFileId: assistantLine?.audio_file_id || "",
      followUpTTSStateText: assistantLine?.audio_file_id ? "语音已生成" : this.data.followUpTTSStateText,
      ttsAlignment: ttsAlignmentFromAssistantLine(assistantLine),
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

  async confirmSavedTranscriptSnapshot(event) {
    if (!this.data.voiceSessionId || !event.message_id || !event.utterance_id) {
      return;
    }
    try {
      const detail = await getMemoirVoiceSession(this.data.voiceSessionId);
      writeMemoirVoiceSessionSnapshot(this.data.voiceSessionId, { detail });
      this.applyVoiceSessionDetail(detail);
      const persisted = detail.messages.some((message) => message.id === event.message_id)
        && detail.utterances.some((utterance) => utterance.id === event.utterance_id);
      this.setData({
        persistenceStateText: persisted ? "已保存" : "已识别，记录同步中",
      });
    } catch (error) {
      console.warn("confirm saved voice transcript failed", error);
      this.setData({ persistenceStateText: "已保存，本地已缓存" });
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
      modeLabel: voiceGuidanceLabel(guidanceMode, currentGuidanceModule),
      transcriptLines: nextLines,
      latestTranscriptText: latestTranscriptText(nextLines),
      assistantPromptText: assistantLine?.text || "",
      ...assistantCopy,
      followUpMessageId: assistantLine?.message_id || assistantLine?.id || "",
      followUpAudioFileId: assistantLine?.audio_file_id || "",
      followUpTTSStateText: assistantLine?.audio_file_id ? "语音已生成" : this.data.followUpTTSStateText,
      ttsAlignment: ttsAlignmentFromAssistantLine(assistantLine),
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
        metadata_json: response.message.metadata_json || {},
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
        ttsAlignment: ttsAlignmentFromMetadata(response.message.metadata_json),
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
      ttsCurrentText: "",
      ttsRemainingText: "",
      ttsProgressPercent: 0,
      ttsAlignment: [],
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
    const existingMetadata = this.data.transcriptLines.find(
      (line) => (line.message_id || line.id) === messageId,
    )?.metadata_json;
    this.setData({
      followUpTTSLoading: true,
      followUpTTSError: "",
      followUpTTSStateText: "正在合成语音",
      ttsSpokenText: "",
      ttsCurrentText: "",
      ttsRemainingText: this.data.assistantPromptText,
      ttsProgressPercent: 0,
      ttsAlignment: ttsAlignmentFromMetadata(existingMetadata),
    });
    try {
      const ttsSettings = readChatTTSSettings();
      const response = await createMemoirAssistantTTS(this.data.voiceSessionId, messageId, {
        voice: ttsSettings.voice || undefined,
        speed_ratio: ttsSettings.speedRatio === 1 ? undefined : ttsSettings.speedRatio,
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
          metadata_json: response.message.metadata_json || line.metadata_json || {},
        };
      });
      const responseAlignment = response.alignment && response.alignment.length
        ? response.alignment
        : ttsAlignmentFromMetadata(response.message.metadata_json);
      this.setData({
        transcriptLines,
        followUpMessageId: response.message.id,
        followUpAudioFileId: response.audio_file_id,
        followUpTTSError: "",
        followUpTTSStateText: "语音已生成",
        ttsAlignment: responseAlignment,
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
    if (this.data.followUpAudioFileId && this.data.ttsAlignment.length) {
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
      const refreshPlaybackProgress = () => {
        if (playToken === this.ttsPlayToken) {
          this.updateFollowUpTTSProgress(this.playbackTimeForProgress(audio), audio.duration);
        }
      };
      audio.onPlay(() => {
        if (playToken === this.ttsPlayToken) {
          this.ttsProgressStartedAtMs = Date.now();
          this.startFollowUpProgressTimer(playToken, audio);
        }
      });
      audio.onTimeUpdate(() => {
        refreshPlaybackProgress();
      });
      audio.onEnded(() => {
        if (playToken === this.ttsPlayToken) {
          this.stopFollowUpProgressTimer();
          this.setData({
            followUpTTSPlaying: false,
            followUpTTSStateText: options.autoListenAfterPlayback ? "播放完成，正在听你说" : "播放完成",
            ttsSpokenText: this.data.assistantPromptText,
            ttsCurrentText: "",
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
          this.stopFollowUpProgressTimer();
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
      this.startFollowUpProgressTimer(playToken, audio);
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
    this.stopFollowUpProgressTimer();
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
    const progress = splitTextByAudioPlaybackProgress(text, currentTime, duration, {
      alignment: this.data.ttsAlignment,
    });
    this.setData({
      ttsSpokenText: progress.spokenText,
      ttsCurrentText: progress.currentText,
      ttsRemainingText: progress.remainingText,
      ttsProgressPercent: progress.progressPercent,
    });
  },

  playbackTimeForProgress(audio) {
    const currentTime = Number(audio.currentTime);
    if (Number.isFinite(currentTime) && currentTime > 0) {
      return currentTime;
    }
    if (this.ttsProgressStartedAtMs > 0) {
      return Math.max(0, (Date.now() - this.ttsProgressStartedAtMs) / 1000);
    }
    return 0;
  },

  startFollowUpProgressTimer(playToken, audio) {
    this.stopFollowUpProgressTimer();
    if (this.ttsProgressStartedAtMs <= 0) {
      this.ttsProgressStartedAtMs = Date.now();
    }
    const timer = setInterval(() => {
      if (playToken !== this.ttsPlayToken || this.audio !== audio || !this.data.followUpTTSPlaying) {
        if (this.ttsProgressTimer === timer) {
          this.stopFollowUpProgressTimer();
        }
        return;
      }
      this.updateFollowUpTTSProgress(this.playbackTimeForProgress(audio), audio.duration);
    }, 250);
    this.ttsProgressTimer = timer;
  },

  stopFollowUpProgressTimer() {
    if (this.ttsProgressTimer !== null) {
      clearInterval(this.ttsProgressTimer);
      this.ttsProgressTimer = null;
    }
    this.ttsProgressStartedAtMs = 0;
  },

  resetFollowUpTTSProgress() {
    this.setData({
      ttsSpokenText: "",
      ttsCurrentText: "",
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
    if (!this.ensureVoiceAuthorization(autoTriggered)) {
      return false;
    }
    if (!streamingClient || this.data.status === "closed") {
      void this.connectGateway();
      this.setData({ autoListenStateText: autoTriggered ? "正在重新连接语音" : this.data.autoListenStateText });
      return false;
    }
    void this.startRecordingAfterPrivacy(autoTriggered);
    return true;
  },

  async startRecordingAfterPrivacy(autoTriggered = false) {
    try {
      await ensureRecorderPrivacyAuthorization();
    } catch (error) {
      const message = error instanceof Error ? error.message : "微信隐私授权失败";
      this.handleError(message, { kind: "recorder", code: "wechat_privacy_authorization_failed", message });
      this.setData({
        recording: false,
        primaryActionText: "继续说话",
        autoListenStateText: autoTriggered ? "自动收音已暂停，等待隐私授权" : this.data.autoListenStateText,
      });
      return false;
    }
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
    if (!this.ensureVoiceAuthorization(false)) {
      return;
    }
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
            wx.switchTab({ url: "/pages/transcripts/index" });
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
    if (event.message_id && event.utterance_id) {
      void this.confirmSavedTranscriptSnapshot(event);
    }
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
