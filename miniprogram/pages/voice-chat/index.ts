import type {
  MemoirVoiceSessionDetail,
  VoiceChatErrorInfo,
  TTSAlignmentSegment,
  VoiceChatTranscriptLine,
  VoiceGatewayServerEvent,
} from "../../domain/voice-chat";
import { normalizeMemoirAsrText } from "../../services/asr-text-normalization";
import { trackAuditAction } from "../../services/audit-tracker";
import { AudioPlaybackController } from "../../services/audio-playback";
import {
  applyAssistantTTSResponse,
  assistantTurnQueueItem,
  type AssistantTurnQueueItem,
  type PreparedAssistantTurnAudio,
} from "../../services/voice-assistant-turn-queue";
import {
  type VoiceAssistantTurnCompletedEvent,
} from "../../services/voice-assistant-turn-stream";
import {
  VoiceAssistantTurnRuntime,
} from "../../services/voice-assistant-turn-runtime";
import type { StreamingSpeechPlaybackFrameEvent } from "../../services/streaming-speech-playback";
import {
  classifyVoiceClientConnectionError,
  type MemoirCaptureStats,
  type VoiceChatConnectionStatus,
} from "../../services/memoir-streaming-asr";
import { StreamingVoiceController } from "../../services/streaming-voice-controller";
import { VoiceBargeInMonitor } from "../../services/voice-barge-in-monitor";
import {
  createMemoirAssistantTTS,
  createMemoirVoiceNoAnswerPrompt,
  getMemoirVoiceSession,
  markMemoirAutomationEventsVoicePlayed,
} from "../../services/memoir-voice-api";
import { splitTextByAudioPlaybackProgress } from "../../services/tts-text-progress";
import { readChatTTSSettings } from "../../services/tts-settings";
import { canAutoStartListeningAfterTTS } from "../../services/voice-chat-auto-listen";
import { voiceGuidanceLabel } from "../../services/memoir-project-profile";
import {
  readVoiceAuthorizationConsent,
  saveVoiceAuthorizationConsent,
  syncVoiceAuthorizationConsent,
  VOICE_AUTHORIZATION_PAGE_URL,
} from "../../services/voice-authorization";
import { ensureRecorderPrivacyAuthorization } from "../../services/wechat-privacy";
import {
  assistantTranscriptState,
  canRequestFollowUp,
  countTextChars,
  coveredFollowUpSourceMessageId,
  errorKindText,
  followUpSourceMessageId,
  formatDuration,
  gatewayStateText,
  latestAssistantMessageId,
  statusText,
  ttsAlignmentFromMetadata,
  upsertTranscriptLine,
} from "../../services/voice-chat-state";
import {
  clearMemoirVoiceSessionPendingClose,
  readMemoirVoiceSessionSnapshot,
  transcriptLinesFromVoiceSessionDetail,
  writeMemoirVoiceSessionPendingClose,
  writeMemoirVoiceSessionSnapshot,
} from "../../stores/memoir-cache";
import {
  type AutoListenGateSnapshot,
  type VoiceChatData,
  initialVoiceChatData,
  voiceChatAutoListenGateSnapshot,
} from "./voice-chat-page-state";

const VOICE_AUTHORIZATION_VALUE = "voice_authorization";

interface CheckboxChangeEvent {
  detail: {
    value: string[];
  };
}

let followUpRequestSerial = 0;
const AUTO_STOP_SILENCE_MS = 3500;
const AUTO_STOP_MIN_RECORDING_MS = 2000;
const AUTO_LISTEN_NO_ANSWER_MS = 15000;
const MAX_CONSECUTIVE_NO_ANSWER_PROMPTS = 2;
const STREAMING_TTS_PROGRESS_HOLD_SECONDS = 0.2;

function queryText(value?: string): string {
  if (!value) {
    return "";
  }
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

Page<VoiceChatData, {
  onLoad(query?: Record<string, string | undefined>): Promise<void>;
  onShow(): void;
  onHide(): void;
  onUnload(): void;
  refreshVoiceAuthorizationConsent(): void;
  changeVoiceAuthorization(event: CheckboxChangeEvent): Promise<void>;
  openVoiceAuthorization(): void;
  ensureVoiceAuthorization(autoTriggered?: boolean): boolean;
  syncVoiceAuthorizationForSession(): Promise<boolean>;
  syncVoiceAuthorizationAndConnect(autoTriggered?: boolean): Promise<void>;
  hydrateVoiceSessionSnapshot(): void;
  refreshVoiceSessionSnapshot(): Promise<MemoirVoiceSessionDetail | null>;
  reconcilePendingClose(detail: MemoirVoiceSessionDetail): void;
  confirmSavedTranscriptSnapshot(event: VoiceGatewayServerEvent): Promise<void>;
  applyVoiceSessionDetail(detail: MemoirVoiceSessionDetail): void;
  persistCurrentTranscriptSnapshot(): void;
  persistPendingVoiceClose(reason: "hide" | "unload" | "connection_error"): void;
  reconnectGateway(): Promise<void>;
  finishAfterDisconnect(): Promise<void>;
  maybeGenerateOpeningFollowUp(): void;
  generateFollowUp(regenerate?: boolean): Promise<void>;
  generateStreamingFollowUp(sourceMessageId: string, regenerate: boolean, requestSerial: number): Promise<void>;
  markStreamingAutomationEventsVoicePlayed(completed: VoiceAssistantTurnCompletedEvent): Promise<void>;
  clearFailedStreamingAssistantTurn(message: string): void;
  applyStreamingAssistantText(text: string, sourceMessageId: string): void;
  applyCompletedStreamingAssistantTurn(event: VoiceAssistantTurnCompletedEvent, sourceMessageId: string): void;
  prepareStreamingAudioPlayback(requestSerial: number): void;
  markStreamingPlaybackError(error: Error, requestSerial: number): void;
  regenerateFollowUp(): Promise<void>;
  skipFollowUp(): void;
  enqueueAssistantTurn(item: AssistantTurnQueueItem): void;
  drainAssistantTurnQueue(): Promise<void>;
  prepareAssistantTurnAudio(item: AssistantTurnQueueItem): Promise<PreparedAssistantTurnAudio>;
  showAssistantTurn(item: AssistantTurnQueueItem, patch?: Partial<VoiceChatData>): void;
  playPreparedAssistantTurn(
    item: AssistantTurnQueueItem,
    prepared: PreparedAssistantTurnAudio,
  ): Promise<void>;
  prepareFollowUpTTS(
    messageId: string,
    forceRegenerate?: boolean,
    autoListenAfterPlayback?: boolean,
  ): Promise<void>;
  playFollowUpAudio(): Promise<void>;
  playAudioFile(
    audioFileId: string,
    options?: {
      autoListenAfterPlayback?: boolean;
      text?: string;
      alignment?: TTSAlignmentSegment[];
      durationSeconds?: number;
    },
  ): Promise<void>;
  stopFollowUpAudio(): void;
  cancelFollowUpTTS(reason?: string): void;
  assistantTurnActiveForBargeIn(): boolean;
  startAssistantBargeInMonitor(requestSerial: number): void;
  stopAssistantBargeInMonitor(): Promise<void>;
  handleAssistantBargeIn(requestSerial: number): Promise<void>;
  markStreamingAudioFrameScheduled(event: StreamingSpeechPlaybackFrameEvent): void;
  updateStreamingTTSProgress(): void;
  streamingPlaybackTimeForProgress(): number;
  updateFollowUpTTSProgress(currentTime: number, duration: number, fallbackDuration?: number): void;
  playbackTimeForProgress(audio: WechatMiniprogram.InnerAudioContext): number;
  streamingProgressPercent(spokenText: string, fullText: string, allowComplete: boolean): number;
  startStreamingProgressTimer(playToken: number): void;
  startFollowUpProgressTimer(
    playToken: number,
    audio: WechatMiniprogram.InnerAudioContext,
    fallbackDuration?: number,
  ): void;
  stopFollowUpProgressTimer(): void;
  resetFollowUpTTSProgress(): void;
  toggleAutoStop(): void;
  autoListenGateSnapshot(): AutoListenGateSnapshot;
  connectGateway(): Promise<void>;
  startRecording(autoTriggered?: boolean): boolean;
  startRecordingAfterPrivacy(autoTriggered?: boolean): Promise<boolean>;
  toggleRecording(): void;
  shouldAutoStartListening(): boolean;
  tryStartAutoListenAfterTTS(): Promise<boolean>;
  cancelPendingNoAnswerPrompt(clearLoading?: boolean): void;
  handleNoAnswerAutoStop(): Promise<void>;
  finishSession(): Promise<void>;
  handleStatus(status: VoiceChatConnectionStatus): void;
  handleCaptureStats(stats: MemoirCaptureStats): void;
  handleGatewayEvent(event: VoiceGatewayServerEvent): void;
  handlePartial(text: string): void;
  handleFinal(event: VoiceGatewayServerEvent): void;
  handleError(message: string, error?: VoiceChatErrorInfo): void;
  audioPlayback: AudioPlaybackController;
  voiceStreaming: StreamingVoiceController;
  assistantBargeInMonitor: VoiceBargeInMonitor;
  assistantTurnRuntime: VoiceAssistantTurnRuntime | null;
  assistantTurnQueue: AssistantTurnQueueItem[];
  assistantTurnQueueRunning: boolean;
  assistantAudioPrepareCount: number;
  assistantStreamLineId: string;
  assistantStreamingAudioStartSeconds: number;
  assistantStreamingAudioEndSeconds: number;
  assistantStreamingAudioDurationSeconds: number;
  ttsPlayToken: number;
  ttsProgressTimer: number | null;
  ttsProgressStartedAtMs: number;
  ttsRequestSerial: number;
  noAnswerPromptInFlight: boolean;
  noAnswerPromptRequestSerial: number;
  consecutiveNoAnswerPrompts: number;
  handledNoAnswerFrameCount: number;
  autoListenPendingAfterPlayback: boolean;
}>({
  audioPlayback: new AudioPlaybackController(),
  voiceStreaming: new StreamingVoiceController(),
  assistantBargeInMonitor: new VoiceBargeInMonitor(),
  assistantTurnRuntime: null,
  assistantTurnQueue: [],
  assistantTurnQueueRunning: false,
  assistantAudioPrepareCount: 0,
  assistantStreamLineId: "",
  assistantStreamingAudioStartSeconds: -1,
  assistantStreamingAudioEndSeconds: 0,
  assistantStreamingAudioDurationSeconds: 0,
  ttsPlayToken: 0,
  ttsProgressTimer: null,
  ttsProgressStartedAtMs: 0,
  ttsRequestSerial: 0,
  noAnswerPromptInFlight: false,
  noAnswerPromptRequestSerial: 0,
  consecutiveNoAnswerPrompts: 0,
  handledNoAnswerFrameCount: -1,
  autoListenPendingAfterPlayback: false,

  data: initialVoiceChatData(),

  autoListenGateSnapshot() {
    const hasClient = this.voiceStreaming.hasClient();
    const canStart = hasClient && canAutoStartListeningAfterTTS({
      autoListenEnabled: this.data.autoListenEnabled,
      status: this.data.status,
      recording: this.data.recording,
      connecting: this.data.connecting,
      followUpLoading: this.data.followUpLoading,
    });
    return voiceChatAutoListenGateSnapshot(this.data, {
      hasClient,
      pendingAfterPlayback: this.autoListenPendingAfterPlayback,
      canStart,
    });
  },

  async onLoad(query) {
    const voiceSessionId = query?.voiceSessionId || "";
    const guidanceMode = query?.mode === "guided_interview" ? "guided_interview" : "free_narration";
    const currentGuidanceModule = query?.module || "";
    const projectTitle = queryText(query?.projectTitle);
    const voiceAuthorizationAgreed = readVoiceAuthorizationConsent();
    this.setData({
      voiceSessionId,
      projectTitle,
      guidanceMode,
      currentGuidanceModule,
      modeLabel: voiceGuidanceLabel(guidanceMode, currentGuidanceModule),
      voiceAuthorizationAgreed,
      primaryActionText: voiceAuthorizationAgreed ? "开始说话" : "同意后开始说话",
    });
    if (!voiceSessionId) {
      this.setData({ error: "缺少本次语音记录" });
      return;
    }
    this.hydrateVoiceSessionSnapshot();
    await this.refreshVoiceSessionSnapshot();
    if (
      this.data.voiceAuthorizationAgreed
      && this.data.status !== "closed"
      && this.data.status !== "error"
    ) {
      await this.syncVoiceAuthorizationAndConnect();
    }
    if (!this.data.voiceAuthorizationAgreed) {
      this.setData({
        statusText: "等待声纹授权",
        autoListenStateText: "同意后才会自动收音",
      });
    }
  },

  onShow() {
    this.refreshVoiceAuthorizationConsent();
  },

  onHide() {
    this.stopAssistantBargeInMonitor();
    if (this.data.recording && this.voiceStreaming.hasClient()) {
      this.voiceStreaming.stopRecording();
      this.persistPendingVoiceClose("hide");
      this.setData({
        recording: false,
        primaryActionText: "继续说话",
        persistenceStateText: "正在保存当前句",
        recoveryNotice: "已暂停收音，返回后会恢复已保存的讲述记录",
      });
    }
  },

  onUnload() {
    followUpRequestSerial += 1;
    this.noAnswerPromptRequestSerial += 1;
    this.noAnswerPromptInFlight = false;
    this.persistCurrentTranscriptSnapshot();
    this.persistPendingVoiceClose("unload");
    this.cancelFollowUpTTS();
    this.stopAssistantBargeInMonitor();
    this.voiceStreaming.close();
  },

  refreshVoiceAuthorizationConsent() {
    const voiceAuthorizationAgreed = readVoiceAuthorizationConsent();
    const patch: Partial<VoiceChatData> = { voiceAuthorizationAgreed };
    if (voiceAuthorizationAgreed && this.data.primaryActionText === "同意后开始说话") {
      patch.primaryActionText = "开始说话";
      patch.error = "";
      patch.autoListenStateText = "小余说完自动听";
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
      && this.data.status !== "closed"
      && this.data.status !== "error"
      && !this.voiceStreaming.hasClient()
    ) {
      void this.syncVoiceAuthorizationAndConnect();
    }
  },

  async changeVoiceAuthorization(event) {
    const agreed = event.detail.value.includes(VOICE_AUTHORIZATION_VALUE);
    const consent = saveVoiceAuthorizationConsent(agreed);
    if (!agreed && this.voiceStreaming.hasClient() && this.data.recording) {
      this.voiceStreaming.stopRecording();
    }
    this.setData({
      voiceAuthorizationAgreed: consent.agreed,
      recording: agreed ? this.data.recording : false,
      error: agreed ? "" : this.data.error,
      primaryActionText: agreed ? "开始说话" : "同意后开始说话",
      autoListenStateText: agreed ? "小余说完自动听" : "同意后才会自动收音",
    });
    if (agreed && this.data.voiceSessionId && !this.voiceStreaming.hasClient() && !this.data.connecting) {
      await this.syncVoiceAuthorizationAndConnect();
    } else {
      void syncVoiceAuthorizationConsent({
        consent,
        sourceSessionId: this.data.voiceSessionId || undefined,
      }).catch((error) => {
        console.warn("sync voice authorization consent change failed", error);
      });
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

  async syncVoiceAuthorizationForSession() {
    if (!readVoiceAuthorizationConsent()) {
      this.ensureVoiceAuthorization(false);
      return false;
    }
    try {
      await syncVoiceAuthorizationConsent({
        sourceSessionId: this.data.voiceSessionId || undefined,
      });
      return true;
    } catch (error) {
      const message = error instanceof Error ? error.message : "声纹授权同步失败";
      this.setData({
        error: message,
        primaryActionText: "继续说话",
        autoListenStateText: "声纹授权同步失败",
      });
      if (typeof wx !== "undefined" && typeof wx.showToast === "function") {
        wx.showToast({ title: "声纹授权同步失败", icon: "none" });
      }
      return false;
    }
  },

  async syncVoiceAuthorizationAndConnect(autoTriggered = false) {
    const synced = await this.syncVoiceAuthorizationForSession();
    if (!synced) {
      if (autoTriggered) {
        this.setData({ autoListenStateText: "自动收音已暂停，等待声纹授权同步" });
      }
      return;
    }
    await this.connectGateway();
  },

  hydrateVoiceSessionSnapshot() {
    const snapshot = readMemoirVoiceSessionSnapshot(this.data.voiceSessionId);
    if (!snapshot.hasSession) {
      return;
    }
    const guidanceMode = snapshot.detail?.session.guidance_mode || this.data.guidanceMode;
    const currentGuidanceModule = snapshot.detail?.session.current_guidance_module || this.data.currentGuidanceModule;
    const coveredSourceMessageId = coveredFollowUpSourceMessageId(snapshot.transcriptLines);
    const lastFollowUpSourceMessageId = coveredSourceMessageId || this.data.lastFollowUpSourceMessageId;
    this.setData({
      guidanceMode,
      currentGuidanceModule,
      modeLabel: voiceGuidanceLabel(guidanceMode, currentGuidanceModule),
      ...assistantTranscriptState(snapshot.transcriptLines, {
        dismissedFollowUpMessageId: this.data.dismissedFollowUpMessageId,
        guidanceMode,
        lastFollowUpSourceMessageId,
        followUpLoading: this.data.followUpLoading,
        currentFollowUpTTSStateText: this.data.followUpTTSStateText,
      }),
      lastFollowUpSourceMessageId,
      recoveryNotice: snapshot.pendingClose ? "正在恢复上次离开前的讲述记录" : this.data.recoveryNotice,
    });
  },

  async refreshVoiceSessionSnapshot() {
    if (!this.data.voiceSessionId) {
      return null;
    }
    try {
      const detail = await getMemoirVoiceSession(this.data.voiceSessionId);
      writeMemoirVoiceSessionSnapshot(this.data.voiceSessionId, { detail });
      this.applyVoiceSessionDetail(detail);
      this.reconcilePendingClose(detail);
      if (detail.session.status !== "stopped" && detail.session.status !== "failed") {
        this.maybeGenerateOpeningFollowUp();
      }
      return detail;
    } catch (error) {
      console.warn("refresh voice session snapshot failed", error);
      return null;
    }
  },

  reconcilePendingClose(detail) {
    const snapshot = readMemoirVoiceSessionSnapshot(this.data.voiceSessionId);
    const pending = snapshot.pendingClose;
    if (!pending) {
      return;
    }
    const detailLines = transcriptLinesFromVoiceSessionDetail(detail);
    const savedTexts = detailLines.map((line) => line.text.trim()).filter(Boolean);
    const pendingLinesSaved = pending.transcript_lines.every((line) => {
      const lineText = line.text.trim();
      if (!lineText) {
        return true;
      }
      return detailLines.some((savedLine) => (
        (line.message_id && savedLine.message_id === line.message_id)
        || (line.utterance_id && savedLine.utterance_id === line.utterance_id)
        || savedLine.text.trim() === lineText
      ));
    });
    const pendingLiveText = pending.live_text.trim();
    const pendingLiveTextSaved = !pendingLiveText || savedTexts.some((text) => text.includes(pendingLiveText));
    if (pendingLinesSaved && pendingLiveTextSaved) {
      clearMemoirVoiceSessionPendingClose(this.data.voiceSessionId);
      this.setData({
        recoveryNotice: "",
        canReconnect: false,
        canFinishAfterDisconnect: false,
      });
      return;
    }
    this.setData({
      recoveryNotice: "上次离开时最后一句可能未保存，请检查讲述记录",
      persistenceStateText: "已恢复已保存记录",
      canReconnect: true,
      canFinishAfterDisconnect: true,
    });
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
    const sessionPatch: Partial<VoiceChatData> = {};
    if (detail.session.status === "stopped") {
      sessionPatch.status = "closed";
      sessionPatch.statusText = statusText("closed");
      sessionPatch.primaryActionText = "已保存";
      sessionPatch.recording = false;
      sessionPatch.persistenceStateText = "已保存";
    }
    if (detail.session.status === "failed") {
      sessionPatch.status = "error";
      sessionPatch.statusText = statusText("error");
      sessionPatch.primaryActionText = "重新开始";
      sessionPatch.recording = false;
      sessionPatch.persistenceStateText = "会话异常";
      sessionPatch.error = detail.session.error_message || "本次语音保存异常，请返回首页重新开始";
    }
    this.setData({
      ...sessionPatch,
      guidanceMode,
      currentGuidanceModule,
      modeLabel: voiceGuidanceLabel(guidanceMode, currentGuidanceModule),
      ...assistantTranscriptState(nextLines, {
        dismissedFollowUpMessageId: this.data.dismissedFollowUpMessageId,
        guidanceMode,
        lastFollowUpSourceMessageId,
        followUpLoading: this.data.followUpLoading,
        currentFollowUpTTSStateText: this.data.followUpTTSStateText,
      }),
      lastFollowUpSourceMessageId,
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

  persistPendingVoiceClose(reason) {
    if (!this.data.voiceSessionId) {
      return;
    }
    if (!this.data.recording && !this.data.liveText.trim() && !this.data.transcriptLines.length) {
      return;
    }
    writeMemoirVoiceSessionPendingClose(this.data.voiceSessionId, {
      reason,
      captured_at: new Date().toISOString(),
      transcript_lines: this.data.transcriptLines,
      live_text: this.data.liveText,
      recording: this.data.recording,
    });
  },

  async reconnectGateway() {
    this.setData({
      recoveryNotice: "",
      canReconnect: false,
      canFinishAfterDisconnect: false,
      error: "",
      errorKindText: "",
    });
    await this.connectGateway();
  },

  async finishAfterDisconnect() {
    this.voiceStreaming.close({ force: true });
    await this.refreshVoiceSessionSnapshot();
    this.setData({
      recording: false,
      status: "closed",
      statusText: statusText("closed"),
      primaryActionText: "已保存",
      canReconnect: false,
      canFinishAfterDisconnect: false,
    });
    wx.switchTab({ url: "/pages/transcripts/index" });
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
    this.cancelFollowUpTTS();
    this.assistantStreamLineId = `assistant-stream-${requestSerial}`;
    this.assistantStreamingAudioStartSeconds = -1;
    this.assistantStreamingAudioEndSeconds = 0;
    this.assistantStreamingAudioDurationSeconds = 0;
    this.setData({
      followUpLoading: true,
      followUpError: "",
      followUpTTSLoading: true,
      followUpTTSError: "",
      followUpTTSStateText: "正在准备小余语音",
      ttsSpokenText: "",
      ttsCurrentText: "",
      ttsRemainingText: "",
      ttsProgressPercent: 0,
      canRequestFollowUp: false,
      persistenceStateText: "小余正在回应",
    });
    try {
      await this.generateStreamingFollowUp(sourceMessageId, regenerate, requestSerial);
    } catch (error) {
      if (requestSerial !== followUpRequestSerial) {
        return;
      }
      this.clearFailedStreamingAssistantTurn(error instanceof Error ? error.message : "小余回应生成失败");
    } finally {
      if (requestSerial === followUpRequestSerial) {
        this.setData({
          followUpLoading: false,
          followUpTTSLoading: false,
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

  async generateStreamingFollowUp(sourceMessageId, regenerate, requestSerial) {
    const runtime = new VoiceAssistantTurnRuntime(this.data.voiceSessionId, {
      onStarted: () => {
        if (requestSerial !== followUpRequestSerial) {
          return;
        }
        this.setData({
          followUpTTSStateText: "小余正在说",
          persistenceStateText: "小余正在回应",
        });
      },
      onTextDelta: (event) => {
        if (requestSerial === followUpRequestSerial) {
          this.applyStreamingAssistantText(event.text, sourceMessageId);
        }
      },
      onAudioFrame: () => {
        if (requestSerial === followUpRequestSerial) {
          this.prepareStreamingAudioPlayback(requestSerial);
        }
      },
      onAudioFrameScheduled: (event) => {
        if (requestSerial === followUpRequestSerial) {
          this.markStreamingAudioFrameScheduled(event);
          this.startStreamingProgressTimer(this.ttsPlayToken);
          this.updateStreamingTTSProgress();
        }
      },
      onAudioDrained: (event) => {
        if (requestSerial === followUpRequestSerial) {
          this.stopAssistantBargeInMonitor();
          const durationSeconds = Number(event.duration_seconds);
          if (Number.isFinite(durationSeconds) && durationSeconds > 0) {
            this.assistantStreamingAudioDurationSeconds = durationSeconds;
          }
          this.stopFollowUpProgressTimer();
          this.setData({
            followUpTTSStateText: "正在整理回应",
          });
        }
      },
      onCompleted: (event) => {
        if (requestSerial === followUpRequestSerial) {
          this.applyCompletedStreamingAssistantTurn(event, sourceMessageId);
        }
      },
      onPlaybackError: (error) => {
        this.markStreamingPlaybackError(error, requestSerial);
      },
    });
    this.assistantTurnRuntime = runtime;
    const { completed, playbackError } = await runtime.start({ regenerate });
    if (requestSerial !== followUpRequestSerial) {
      return;
    }
    this.assistantTurnRuntime = null;
    if (!playbackError && completed.expects_answer !== false) {
      this.autoListenPendingAfterPlayback = true;
    }
    if (!playbackError) {
      void this.markStreamingAutomationEventsVoicePlayed(completed);
    }
    this.stopAssistantBargeInMonitor();
    this.stopFollowUpProgressTimer();
    this.setData({
      dismissedFollowUpMessageId: "",
      followUpLoading: false,
      followUpTTSLoading: false,
      followUpTTSPlaying: false,
      followUpTTSError: playbackError ? playbackError.message : "",
      followUpTTSStateText: playbackError
        ? "语音播放失败，可重播"
        : (completed.expects_answer !== false ? "播放完成，正在听你说" : "播放完成"),
      ttsSpokenText: playbackError ? this.data.ttsSpokenText : completed.text,
      ttsCurrentText: "",
      ttsRemainingText: playbackError ? this.data.ttsRemainingText : "",
      ttsProgressPercent: playbackError ? this.data.ttsProgressPercent : 100,
      persistenceStateText: playbackError
        ? "语音播放失败"
        : (completed.expects_answer === false ? "小余已回应" : "等待回答"),
      lastFollowUpSourceMessageId: sourceMessageId,
    });
    if (!playbackError && completed.expects_answer !== false) {
      await this.tryStartAutoListenAfterTTS();
    }
  },

  async markStreamingAutomationEventsVoicePlayed(completed) {
    const eventIds = Array.isArray(completed.progress_voice_selected_event_ids)
      ? completed.progress_voice_selected_event_ids.filter((item) => typeof item === "string" && item)
      : [];
    if (!completed.project_id || !eventIds.length || !completed.message_id) {
      return;
    }
    try {
      await markMemoirAutomationEventsVoicePlayed(completed.project_id, {
        event_ids: eventIds,
        voice_session_id: completed.voice_session_id || this.data.voiceSessionId,
        message_id: completed.message_id,
      });
    } catch (error) {
      console.warn("mark automation voice playback failed", error);
    }
  },

  clearFailedStreamingAssistantTurn(message) {
    this.assistantTurnRuntime?.stopPlayback();
    this.assistantTurnRuntime = null;
    this.stopFollowUpAudio();
    const transcriptLines = this.assistantStreamLineId
      ? this.data.transcriptLines.filter((line) => line.id !== this.assistantStreamLineId)
      : this.data.transcriptLines;
    this.assistantStreamLineId = "";
    this.assistantStreamingAudioStartSeconds = -1;
    this.assistantStreamingAudioEndSeconds = 0;
    this.assistantStreamingAudioDurationSeconds = 0;
    this.setData({
      ...assistantTranscriptState(transcriptLines, {
        guidanceMode: this.data.guidanceMode,
        lastFollowUpSourceMessageId: this.data.lastFollowUpSourceMessageId,
        followUpLoading: false,
        assistantPromptText: "",
        followUpMessageId: "",
        followUpAudioFileId: "",
        followUpTTSStateText: "",
        ttsAlignment: [],
        assistantExpectsAnswer: true,
        canRequestFollowUp: true,
      }),
      dismissedFollowUpMessageId: "",
      followUpError: message,
      followUpTTSLoading: false,
      followUpTTSPlaying: false,
      followUpTTSError: "",
      followUpTTSStateText: "",
      ttsSpokenText: "",
      ttsCurrentText: "",
      ttsRemainingText: "",
      ttsProgressPercent: 0,
      persistenceStateText: "回应失败",
    });
  },

  applyStreamingAssistantText(text, sourceMessageId) {
    const line: VoiceChatTranscriptLine = {
      id: this.assistantStreamLineId || `assistant-stream-${Date.now()}`,
      role: "assistant",
      text,
      message_id: "",
      audio_file_id: "",
      metadata_json: {
        source: "ai.streaming_assistant_turn",
        source_message_id: sourceMessageId,
        streaming: true,
      },
    };
    const transcriptLines = upsertTranscriptLine(this.data.transcriptLines, line);
    this.setData({
      ...assistantTranscriptState(transcriptLines, {
        guidanceMode: this.data.guidanceMode,
        lastFollowUpSourceMessageId: this.data.lastFollowUpSourceMessageId,
        followUpLoading: true,
        assistantPromptText: text,
        followUpMessageId: "",
        followUpAudioFileId: "",
        followUpTTSStateText: this.data.followUpTTSStateText,
        ttsAlignment: [],
        assistantExpectsAnswer: true,
        canRequestFollowUp: false,
      }),
      followUpTTSError: "",
      persistenceStateText: "小余正在回应",
    });
  },

  applyCompletedStreamingAssistantTurn(event, sourceMessageId) {
    const metadata = {
      source: "ai.streaming_assistant_turn",
      source_message_id: sourceMessageId,
      primary_intent: event.primary_intent,
      intent_tags: event.intent_tags || [],
      expects_answer: event.expects_answer,
      usage: event.usage || {},
      finish_reason: event.finish_reason || "",
      progress_update: event.progress_update || null,
      progress_update_used: Boolean(event.progress_update_used),
    };
    const finalLine: VoiceChatTranscriptLine = {
      id: event.message_id,
      role: "assistant",
      text: event.text,
      message_id: event.message_id,
      audio_file_id: event.audio_file_id || "",
      metadata_json: metadata,
    };
    const withoutTemporaryLine = this.data.transcriptLines.filter((line) => (
      line.id !== this.assistantStreamLineId
      && line.message_id !== event.message_id
    ));
    const transcriptLines = upsertTranscriptLine(withoutTemporaryLine, finalLine);
    this.setData({
      ...assistantTranscriptState(transcriptLines, {
        guidanceMode: this.data.guidanceMode,
        lastFollowUpSourceMessageId: sourceMessageId,
        followUpLoading: true,
        assistantPromptText: event.text,
        followUpMessageId: event.message_id,
        followUpAudioFileId: event.audio_file_id || "",
        followUpTTSStateText: this.data.followUpTTSStateText,
        ttsAlignment: [],
        assistantExpectsAnswer: event.expects_answer !== false,
        canRequestFollowUp: false,
      }),
      followUpTTSError: "",
    });
    writeMemoirVoiceSessionSnapshot(this.data.voiceSessionId, { transcriptLines });
  },

  prepareStreamingAudioPlayback(requestSerial) {
    if (requestSerial !== followUpRequestSerial) {
      return;
    }
    let playToken = this.ttsPlayToken;
    if (!this.audioPlayback.isCurrentToken(playToken)) {
      playToken = this.audioPlayback.nextToken();
      this.ttsPlayToken = playToken;
    }
    const fullText = this.data.assistantPromptText;
    const firstStreamingFrame = this.assistantStreamingAudioStartSeconds < 0;
    this.setData({
      followUpTTSLoading: false,
      followUpTTSPlaying: true,
      followUpTTSError: "",
      followUpTTSStateText: "小余正在读问题",
      ...(firstStreamingFrame ? {
        ttsSpokenText: "",
        ttsCurrentText: "",
        ttsRemainingText: fullText,
        ttsProgressPercent: 0,
      } : {}),
    });
    this.startAssistantBargeInMonitor(requestSerial);
  },

  markStreamingPlaybackError(error, requestSerial) {
    if (requestSerial !== followUpRequestSerial) {
      return;
    }
    this.stopAssistantBargeInMonitor();
    this.stopFollowUpProgressTimer();
    this.setData({
      followUpTTSError: error.message,
      followUpTTSStateText: "播放失败",
    });
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
      assistantExpectsAnswer: true,
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
      persistenceStateText: "已跳过回应",
    });
  },

  enqueueAssistantTurn(item) {
    if (!item.messageId) {
      return;
    }
    const transcriptLines = upsertTranscriptLine(this.data.transcriptLines, item.line);
    this.setData({
      transcriptLines,
    });
    writeMemoirVoiceSessionSnapshot(this.data.voiceSessionId, { transcriptLines });
    item.prepare = this.prepareAssistantTurnAudio(item);
    this.assistantTurnQueue.push(item);
    if (!this.assistantTurnQueueRunning) {
      void this.drainAssistantTurnQueue();
    }
  },

  async drainAssistantTurnQueue() {
    if (this.assistantTurnQueueRunning) {
      return;
    }
    this.assistantTurnQueueRunning = true;
    try {
      while (this.assistantTurnQueue.length) {
        const item = this.assistantTurnQueue.shift();
        if (!item) {
          continue;
        }
        this.showAssistantTurn(item, {
          followUpTTSStateText: item.prepare ? "正在准备语音" : this.data.followUpTTSStateText,
        });
        try {
          const prepared = await item.prepare;
          if (!prepared) {
            continue;
          }
          await this.playPreparedAssistantTurn(item, prepared);
        } catch (error) {
          if (error instanceof Error && error.message === "__assistant_audio_cancelled__") {
            continue;
          }
          this.setData({
            followUpTTSError: error instanceof Error ? error.message : "小余语音生成失败",
            followUpTTSStateText: "语音暂不可用",
          });
        }
      }
    } finally {
      this.assistantTurnQueueRunning = false;
    }
  },

  async prepareAssistantTurnAudio(item) {
    if (!this.data.voiceSessionId || !item.messageId) {
      throw new Error("缺少要合成的 AI 回应");
    }
    const requestSerial = this.ttsRequestSerial;
    this.assistantAudioPrepareCount += 1;
    this.setData({
      followUpTTSLoading: true,
      followUpTTSError: "",
      followUpTTSStateText: "正在合成语音",
    });
    try {
      const ttsSettings = readChatTTSSettings();
      const response = await createMemoirAssistantTTS(this.data.voiceSessionId, item.messageId, {
        voice: ttsSettings.voice || undefined,
        speed_ratio: ttsSettings.speedRatio === 1 ? undefined : ttsSettings.speedRatio,
        force_regenerate: item.forceRegenerate,
      });
      if (requestSerial !== this.ttsRequestSerial) {
        throw new Error("__assistant_audio_cancelled__");
      }
      const { lines, prepared } = applyAssistantTTSResponse(this.data.transcriptLines, response);
      this.setData({
        transcriptLines: lines,
        followUpTTSError: "",
        followUpTTSStateText: this.data.followUpTTSPlaying ? "下一句语音已准备" : "语音已生成",
      });
      writeMemoirVoiceSessionSnapshot(this.data.voiceSessionId, { transcriptLines: lines });
      return prepared;
    } catch (error) {
      throw error instanceof Error ? error : new Error("小余语音生成失败");
    } finally {
      this.assistantAudioPrepareCount = Math.max(0, this.assistantAudioPrepareCount - 1);
      if (this.assistantAudioPrepareCount === 0) {
        this.setData({ followUpTTSLoading: false });
      }
    }
  },

  showAssistantTurn(item, patch = {}) {
    const transcriptLines = upsertTranscriptLine(this.data.transcriptLines, item.line);
    this.setData({
      ...assistantTranscriptState(transcriptLines, {
        guidanceMode: this.data.guidanceMode,
        lastFollowUpSourceMessageId: this.data.lastFollowUpSourceMessageId,
        followUpLoading: this.data.followUpLoading,
        assistantPromptText: item.text,
        followUpMessageId: item.messageId,
        followUpAudioFileId: item.line.audio_file_id || "",
        followUpTTSStateText: this.data.followUpTTSStateText,
        ttsAlignment: ttsAlignmentFromMetadata(item.line.metadata_json),
        assistantExpectsAnswer: item.expectsAnswer,
        canRequestFollowUp: false,
      }),
      ...patch,
    });
  },

  async playPreparedAssistantTurn(item, prepared) {
    this.showAssistantTurn({
      ...item,
      line: {
        ...item.line,
        audio_file_id: prepared.audioFileId,
        metadata_json: prepared.metadata || item.line.metadata_json,
      },
    }, {
      followUpAudioFileId: prepared.audioFileId,
      ttsAlignment: prepared.alignment,
      followUpTTSStateText: "语音已生成",
    });
    await this.playAudioFile(prepared.audioFileId, {
      autoListenAfterPlayback: item.autoListenAfterPlayback,
      text: item.text,
      alignment: prepared.alignment,
    });
  },

  async prepareFollowUpTTS(messageId, forceRegenerate = false, autoListenAfterPlayback = true) {
    if (!this.data.voiceSessionId || !messageId) {
      return;
    }
    const line = this.data.transcriptLines.find((item) => (item.message_id || item.id) === messageId);
    if (!line) {
      return;
    }
    this.enqueueAssistantTurn({
      messageId,
      text: line.text,
      expectsAnswer: line.metadata_json?.expects_answer !== false,
      autoListenAfterPlayback,
      forceRegenerate,
      line,
    });
  },

  async playFollowUpAudio() {
    if (this.data.followUpTTSLoading) {
      return;
    }
    if (this.data.followUpAudioFileId) {
      await this.playAudioFile(this.data.followUpAudioFileId, {
        autoListenAfterPlayback: false,
        text: this.data.assistantPromptText,
        alignment: this.data.ttsAlignment,
        durationSeconds: this.assistantStreamingAudioDurationSeconds,
      });
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
    const playToken = this.audioPlayback.nextToken();
    this.ttsPlayToken = playToken;
    this.setData({
      assistantPromptText: options.text || this.data.assistantPromptText,
      ttsAlignment: options.alignment || this.data.ttsAlignment,
      followUpTTSPlaying: true,
      followUpTTSError: "",
      followUpTTSStateText: "小余正在读问题",
      ttsSpokenText: "",
      ttsCurrentText: "",
      ttsRemainingText: options.text || this.data.assistantPromptText,
      ttsProgressPercent: 0,
    });
    const fallbackDuration = Number.isFinite(options.durationSeconds) && Number(options.durationSeconds) > 0
      ? Number(options.durationSeconds)
      : 0;
    this.updateFollowUpTTSProgress(0, 0, fallbackDuration);
    try {
      await this.audioPlayback.playFile(audioFileId, playToken, {
        onPlay: (audio) => {
          if (playToken === this.ttsPlayToken) {
            this.ttsProgressStartedAtMs = Date.now();
            this.startFollowUpProgressTimer(playToken, audio, fallbackDuration);
            this.startAssistantBargeInMonitor(followUpRequestSerial);
          }
        },
        onTimeUpdate: (audio) => {
          if (playToken === this.ttsPlayToken) {
            this.updateFollowUpTTSProgress(
              this.playbackTimeForProgress(audio),
              audio.duration,
              fallbackDuration,
            );
          }
        },
        onPlayRequested: (audio) => {
          this.startFollowUpProgressTimer(playToken, audio, fallbackDuration);
        },
        onEnded: () => {
          if (playToken === this.ttsPlayToken) {
            this.stopAssistantBargeInMonitor();
            if (options.autoListenAfterPlayback) {
              this.autoListenPendingAfterPlayback = true;
            }
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
          if (playToken === this.ttsPlayToken && options.autoListenAfterPlayback) {
            void this.tryStartAutoListenAfterTTS();
          }
        },
        onError: () => {
          if (playToken === this.ttsPlayToken) {
            this.stopAssistantBargeInMonitor();
            this.stopFollowUpProgressTimer();
            this.setData({
              followUpTTSPlaying: false,
              followUpTTSError: "小余语音播放失败",
              followUpTTSStateText: "播放失败",
            });
          }
        },
        errorMessage: "小余语音播放失败",
      });
    } catch (error) {
      if (playToken === this.ttsPlayToken) {
        this.stopAssistantBargeInMonitor();
        this.setData({
          followUpTTSPlaying: false,
          followUpTTSError: error instanceof Error ? error.message : "小余语音播放失败",
          followUpTTSStateText: "播放失败",
        });
      }
    }
  },

  stopFollowUpAudio() {
    this.stopAssistantBargeInMonitor();
    this.audioPlayback.stop();
    this.assistantTurnRuntime?.stopPlayback();
    this.ttsPlayToken = this.audioPlayback.currentToken();
    this.autoListenPendingAfterPlayback = false;
    this.stopFollowUpProgressTimer();
    if (this.data.followUpTTSPlaying) {
      this.setData({ followUpTTSPlaying: false });
    }
    this.resetFollowUpTTSProgress();
  },

  cancelFollowUpTTS(reason = "cancelled") {
    this.ttsRequestSerial += 1;
    const partialAssistantLineId = this.assistantStreamLineId;
    const transcriptLines = partialAssistantLineId
      ? this.data.transcriptLines.filter((line) => line.id !== partialAssistantLineId)
      : this.data.transcriptLines;
    this.assistantTurnRuntime?.cancel(reason);
    this.assistantTurnRuntime = null;
    this.assistantStreamLineId = "";
    this.assistantStreamingAudioStartSeconds = -1;
    this.assistantStreamingAudioEndSeconds = 0;
    this.assistantStreamingAudioDurationSeconds = 0;
    this.assistantTurnQueue = [];
    this.assistantAudioPrepareCount = 0;
    this.stopFollowUpAudio();
    if (partialAssistantLineId) {
      this.setData({
        ...assistantTranscriptState(transcriptLines, {
          guidanceMode: this.data.guidanceMode,
          lastFollowUpSourceMessageId: this.data.lastFollowUpSourceMessageId,
          followUpLoading: false,
          assistantPromptText: "",
          followUpMessageId: "",
          followUpAudioFileId: "",
          followUpTTSStateText: "",
          ttsAlignment: [],
          assistantExpectsAnswer: true,
          canRequestFollowUp: canRequestFollowUp(
            transcriptLines,
            this.data.guidanceMode,
            this.data.lastFollowUpSourceMessageId,
            false,
          ),
        }),
      });
    }
    if (this.data.followUpTTSLoading || this.data.followUpTTSPlaying) {
      this.setData({
        followUpTTSLoading: false,
        followUpTTSPlaying: false,
        followUpTTSStateText: this.data.followUpTTSStateText ? "语音已停止" : "",
      });
    }
  },

  assistantTurnActiveForBargeIn() {
    return Boolean(this.assistantTurnRuntime)
      || this.data.followUpLoading
      || this.data.followUpTTSLoading
      || this.data.followUpTTSPlaying;
  },

  startAssistantBargeInMonitor(requestSerial) {
    if (
      this.data.recording
      || !this.assistantTurnActiveForBargeIn()
      || this.assistantBargeInMonitor.isRunning()
    ) {
      return;
    }
    const started = this.assistantBargeInMonitor.start({
      onBargeIn: () => {
        void this.handleAssistantBargeIn(requestSerial);
      },
      onError: () => {
        if (
          requestSerial === followUpRequestSerial
          && !this.data.recording
          && this.assistantTurnActiveForBargeIn()
        ) {
          this.setData({ autoListenStateText: "可以点按钮回答" });
        }
      },
    });
    if (started) {
      this.setData({ autoListenStateText: "想说时可以直接开口" });
    }
  },

  stopAssistantBargeInMonitor() {
    return this.assistantBargeInMonitor.stopAndWait();
  },

  async handleAssistantBargeIn(requestSerial) {
    if (
      requestSerial !== followUpRequestSerial
      || this.data.recording
      || !this.assistantTurnActiveForBargeIn()
    ) {
      await this.stopAssistantBargeInMonitor();
      return;
    }
    this.setData({
      autoListenStateText: "听到您说话了",
    });
    if (!this.ensureVoiceAuthorization(false)) {
      await this.stopAssistantBargeInMonitor();
      return;
    }
    await this.startRecordingAfterPrivacy(false);
  },

  markStreamingAudioFrameScheduled(event) {
    if (this.assistantStreamingAudioStartSeconds < 0) {
      this.assistantStreamingAudioStartSeconds = event.startAtSeconds;
    }
    const frameEndSeconds = event.startAtSeconds + event.durationSeconds;
    this.assistantStreamingAudioEndSeconds = Math.max(
      this.assistantStreamingAudioEndSeconds,
      frameEndSeconds,
    );
    this.assistantStreamingAudioDurationSeconds = Math.max(
      this.assistantStreamingAudioDurationSeconds,
      this.assistantStreamingAudioEndSeconds - this.assistantStreamingAudioStartSeconds,
    );
  },

  updateStreamingTTSProgress() {
    const text = this.data.assistantPromptText;
    if (!text) {
      this.resetFollowUpTTSProgress();
      return;
    }
    const currentTime = this.streamingPlaybackTimeForProgress();
    const scheduledDuration = this.assistantStreamingAudioStartSeconds < 0
      ? 0
      : Math.max(0, this.assistantStreamingAudioEndSeconds - this.assistantStreamingAudioStartSeconds);
    const duration = Math.max(scheduledDuration, currentTime + STREAMING_TTS_PROGRESS_HOLD_SECONDS);
    const progress = splitTextByAudioPlaybackProgress(text, currentTime, duration, {
      alignment: [],
    });
    this.setData({
      ttsSpokenText: progress.spokenText,
      ttsCurrentText: progress.currentText,
      ttsRemainingText: progress.remainingText,
      ttsProgressPercent: Math.min(progress.progressPercent, 99),
    });
  },

  streamingPlaybackTimeForProgress() {
    if (this.assistantStreamingAudioStartSeconds < 0) {
      return 0;
    }
    const currentTimeSeconds = this.assistantTurnRuntime?.stats()?.currentTimeSeconds;
    const current = Number.isFinite(currentTimeSeconds) ? Number(currentTimeSeconds) : 0;
    return Math.max(0, current - this.assistantStreamingAudioStartSeconds);
  },

  updateFollowUpTTSProgress(currentTime, duration, fallbackDuration = 0) {
    const text = this.data.assistantPromptText;
    if (!text) {
      this.resetFollowUpTTSProgress();
      return;
    }
    const effectiveDuration = Number.isFinite(duration) && duration > 0 ? duration : fallbackDuration;
    const progress = splitTextByAudioPlaybackProgress(text, currentTime, effectiveDuration, {
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

  streamingProgressPercent(spokenText: string, fullText: string, allowComplete: boolean) {
    const totalChars = countTextChars(fullText);
    if (totalChars <= 0) {
      return 0;
    }
    const spokenChars = Math.min(totalChars, countTextChars(spokenText));
    const percent = Math.max(0, Math.round((spokenChars / totalChars) * 100));
    return allowComplete ? percent : Math.min(percent, 99);
  },

  startStreamingProgressTimer(playToken: number) {
    if (this.ttsProgressTimer !== null) {
      return;
    }
    this.stopFollowUpProgressTimer();
    if (playToken !== this.ttsPlayToken) {
      return;
    }
    const timer = setInterval(() => {
      if (
        playToken !== this.ttsPlayToken
        || !this.assistantTurnRuntime
        || !this.data.followUpTTSPlaying
      ) {
        if (this.ttsProgressTimer === timer) {
          this.stopFollowUpProgressTimer();
        }
        return;
      }
      this.updateStreamingTTSProgress();
    }, 250);
    this.ttsProgressTimer = timer;
  },

  startFollowUpProgressTimer(playToken, audio, fallbackDuration = 0) {
    this.stopFollowUpProgressTimer();
    if (this.ttsProgressStartedAtMs <= 0) {
      this.ttsProgressStartedAtMs = Date.now();
    }
    const timer = setInterval(() => {
      if (
        playToken !== this.ttsPlayToken
        || this.audioPlayback.currentAudio() !== audio
        || !this.data.followUpTTSPlaying
      ) {
        if (this.ttsProgressTimer === timer) {
          this.stopFollowUpProgressTimer();
        }
        return;
      }
      this.updateFollowUpTTSProgress(
        this.playbackTimeForProgress(audio),
        audio.duration,
        fallbackDuration,
      );
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
    this.voiceStreaming.configureAutoStop(autoStopEnabled ? AUTO_STOP_SILENCE_MS : 0);
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
    let connected = false;
    try {
      await this.voiceStreaming.connect({
        voiceSessionId: this.data.voiceSessionId,
        guidanceMode: this.data.guidanceMode,
        currentGuidanceModule: this.data.currentGuidanceModule,
        autoStopSilenceMs: this.data.autoStopEnabled ? AUTO_STOP_SILENCE_MS : 0,
        minAutoStopRecordingMs: AUTO_STOP_MIN_RECORDING_MS,
        callbacks: {
          onStatus: (status) => {
            this.handleStatus(status);
          },
          onCaptureStats: (stats) => {
            this.handleCaptureStats(stats);
          },
          onEvent: (event) => {
            this.handleGatewayEvent(event);
          },
          onPartial: (text) => {
            this.handlePartial(text);
          },
          onFinal: (event) => {
            this.handleFinal(event);
          },
          onError: (message, error) => {
            this.handleError(message, error);
          },
        },
      });
      connected = true;
    } catch (error) {
      const message = error instanceof Error ? error.message : "语音连接失败";
      this.handleError(message, classifyVoiceClientConnectionError(message));
    } finally {
      this.setData({ connecting: false });
      if (connected) {
        await this.tryStartAutoListenAfterTTS();
      }
    }
  },

  startRecording(autoTriggered = false) {
    if (!this.ensureVoiceAuthorization(autoTriggered)) {
      return false;
    }
    if (!this.voiceStreaming.hasClient() || this.data.status === "closed" || this.data.status === "error") {
      void this.syncVoiceAuthorizationAndConnect(autoTriggered);
      this.setData({ autoListenStateText: autoTriggered ? "正在重新连接语音" : this.data.autoListenStateText });
      return false;
    }
    void this.startRecordingAfterPrivacy(autoTriggered);
    return true;
  },

  async startRecordingAfterPrivacy(autoTriggered = false) {
    const interruptingAssistant = !autoTriggered && this.assistantTurnActiveForBargeIn();
    if (!autoTriggered) {
      this.consecutiveNoAnswerPrompts = 0;
      this.cancelPendingNoAnswerPrompt(!interruptingAssistant);
    }
    if (interruptingAssistant) {
      followUpRequestSerial += 1;
      this.cancelFollowUpTTS("interrupted_by_user");
      this.setData({
        followUpLoading: false,
        followUpError: "",
        followUpTTSStateText: "正在听您说",
        persistenceStateText: "正在听你说",
      });
    }
    await this.stopAssistantBargeInMonitor();
    if (!(await this.syncVoiceAuthorizationForSession())) {
      return false;
    }
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
    if (!this.voiceStreaming.hasClient() || this.data.status === "closed" || this.data.status === "error") {
      void this.syncVoiceAuthorizationAndConnect(autoTriggered);
      this.setData({ autoListenStateText: autoTriggered ? "正在重新连接语音" : this.data.autoListenStateText });
      return false;
    }
    const started = this.voiceStreaming.startRecording({
      noVoiceActivityTimeoutMs: autoTriggered ? AUTO_LISTEN_NO_ANSWER_MS : 0,
    });
    if (!started) {
      this.setData({
        error: "语音未就绪，请重新连接",
        status: "error",
        statusText: statusText("error"),
        primaryActionText: "重新连接",
        canReconnect: true,
        autoListenStateText: autoTriggered ? "自动收音未就绪" : this.data.autoListenStateText,
      });
      return false;
    }
    if (!interruptingAssistant) {
      this.cancelFollowUpTTS();
    }
    this.handledNoAnswerFrameCount = -1;
    this.setData({
      recording: true,
      error: "",
      liveText: "",
      liveCharCount: 0,
      primaryActionText: "暂停收音",
      autoListenStateText: autoTriggered ? "小余说完，正在听您说" : "小余说完自动听",
    });
    trackAuditAction(
      "voice_chat.recording_start",
      { auto_triggered: autoTriggered },
      { voiceSessionId: this.data.voiceSessionId },
    );
    return true;
  },

  toggleRecording() {
    if (!this.ensureVoiceAuthorization(false)) {
      return;
    }
    if (!this.voiceStreaming.hasClient() || this.data.status === "closed" || this.data.status === "error") {
      void this.syncVoiceAuthorizationAndConnect(false);
      return;
    }
    if (this.data.recording) {
      this.voiceStreaming.stopRecording();
      this.setData({ recording: false, primaryActionText: "继续说话" });
      return;
    }
    this.startRecording(false);
  },

  shouldAutoStartListening() {
    return this.autoListenGateSnapshot().canStart;
  },

  async tryStartAutoListenAfterTTS() {
    const gate = this.autoListenGateSnapshot();
    if (!this.autoListenPendingAfterPlayback || !gate.canStart) {
      return false;
    }
    this.autoListenPendingAfterPlayback = false;
    return this.startRecordingAfterPrivacy(true);
  },

  cancelPendingNoAnswerPrompt(clearLoading = true) {
    this.noAnswerPromptRequestSerial += 1;
    if (!this.noAnswerPromptInFlight) {
      return;
    }
    this.noAnswerPromptInFlight = false;
    if (clearLoading) {
      this.setData({ followUpLoading: false });
    }
  },

  async handleNoAnswerAutoStop() {
    if (this.noAnswerPromptInFlight || !this.data.voiceSessionId) {
      return;
    }
    this.noAnswerPromptInFlight = true;
    const requestSerial = this.noAnswerPromptRequestSerial + 1;
    this.noAnswerPromptRequestSerial = requestSerial;
    const attempt = Math.min(this.consecutiveNoAnswerPrompts + 1, MAX_CONSECUTIVE_NO_ANSWER_PROMPTS);
    const autoListenAfterPlayback = attempt < MAX_CONSECUTIVE_NO_ANSWER_PROMPTS;
    this.consecutiveNoAnswerPrompts = attempt;
    this.setData({
      followUpLoading: true,
      followUpError: "",
      canRequestFollowUp: false,
      persistenceStateText: "小余正在确认",
      autoListenStateText: "没听到回答，小余再问一句",
    });
    try {
      const response = await createMemoirVoiceNoAnswerPrompt(this.data.voiceSessionId, { attempt });
      if (requestSerial !== this.noAnswerPromptRequestSerial) {
        return;
      }
      this.enqueueAssistantTurn(assistantTurnQueueItem(response, { autoListenAfterPlayback }));
      this.setData({
        dismissedFollowUpMessageId: "",
        followUpTTSError: "",
        persistenceStateText: autoListenAfterPlayback ? "小余正在确认" : "已暂停收音",
        autoListenStateText: autoListenAfterPlayback ? "小余问完会继续听" : "已暂停，可点继续说话",
      });
    } catch (error) {
      if (requestSerial !== this.noAnswerPromptRequestSerial) {
        return;
      }
      this.setData({
        followUpError: error instanceof Error ? error.message : "小余确认生成失败",
        persistenceStateText: "确认失败",
        autoListenStateText: "没听到回答，可点继续说话",
        canRequestFollowUp: canRequestFollowUp(
          this.data.transcriptLines,
          this.data.guidanceMode,
          this.data.lastFollowUpSourceMessageId,
          false,
        ),
      });
    } finally {
      if (requestSerial === this.noAnswerPromptRequestSerial) {
        this.noAnswerPromptInFlight = false;
        this.setData({ followUpLoading: false });
      }
    }
  },

  async finishSession() {
    if (this.data.finishingSession) {
      return;
    }
    this.cancelFollowUpTTS();
    this.setData({
      finishingSession: true,
      error: "",
      recording: false,
      status: "stopping",
      statusText: statusText("stopping"),
      primaryActionText: "保存中",
      persistenceStateText: this.data.recording ? "正在保存最后一句" : "正在保存",
    });
    if (!this.voiceStreaming.hasClient()) {
      await this.refreshVoiceSessionSnapshot();
      this.setData({ finishingSession: false });
      return;
    }
    try {
      await this.voiceStreaming.finishSession();
      trackAuditAction("voice_chat.finish", {}, { voiceSessionId: this.data.voiceSessionId });
      this.setData({
        recording: false,
        primaryActionText: "已保存",
        status: "closed",
        statusText: statusText("closed"),
        persistenceStateText: "正在同步",
      });
      await this.refreshVoiceSessionSnapshot();
      this.setData({ persistenceStateText: "已保存" });
      wx.showModal({
        title: "本次讲述已保存",
        content: "可以到讲述记录里查看和整理文字。",
        confirmText: "查看记录",
        cancelText: "留在本页",
        success: (result) => {
          if (result.confirm) {
            wx.switchTab({ url: "/pages/transcripts/index" });
          }
        },
      });
    } catch (error) {
      await this.refreshVoiceSessionSnapshot();
      this.handleError(error instanceof Error ? error.message : "语音保存失败");
    } finally {
      this.setData({ finishingSession: false });
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
    let autoStopStateText = `自动收尾开 · 静音 ${formatDuration(stats.silentElapsedMs || 0)}`;
    if (stats.autoStopped && stats.autoStopReason === "no_voice") {
      autoStopStateText = "未听到回答，已暂停收音";
    } else if (!this.data.autoStopEnabled) {
      autoStopStateText = "自动收尾关";
    } else if (stats.autoStopped) {
      autoStopStateText = "检测到停顿，已自动收尾";
    }
    const patch: Partial<VoiceChatData> = {
      captureStateText: labels[stats.state],
      captureFrameCount: stats.frameCount,
      captureKilobytes: Math.round(stats.audioBytesSent / 1024),
      captureDurationText: formatDuration(stats.recordingElapsedMs),
      gatewayStateText: gatewayStateText(stats.gatewayState),
      audioLevelPercent: Math.min(100, Math.round(Math.sqrt(stats.audioLevel || 0) * 100)),
      autoStopStateText,
    };
    const noAnswerAutoStopped = stats.autoStopped && stats.autoStopReason === "no_voice";
    const noAnswerPromptAlreadyHandled = noAnswerAutoStopped
      && this.handledNoAnswerFrameCount === stats.frameCount;
    if (noAnswerAutoStopped && !noAnswerPromptAlreadyHandled) {
      patch.autoListenStateText = "没听到回答，已暂停，可点继续说话";
      patch.persistenceStateText = "未听到回答";
      patch.primaryActionText = "继续说话";
    }
    this.setData(patch);
    if (noAnswerAutoStopped && !noAnswerPromptAlreadyHandled) {
      this.handledNoAnswerFrameCount = stats.frameCount;
      void this.handleNoAnswerAutoStop();
    }
  },

  handleGatewayEvent(event) {
    const nextData: Partial<VoiceChatData> = {};
    if (event.state) {
      nextData.gatewayStateText = gatewayStateText(event.state);
    }
    if (event.type === "audio.ended") {
      nextData.recording = false;
      nextData.primaryActionText = "继续说话";
      nextData.persistenceStateText = this.data.persistenceStateText === "已保存"
        || this.data.persistenceStateText === "未听到回答"
        || this.data.persistenceStateText === "小余正在确认"
        ? this.data.persistenceStateText
        : "正在保存";
    }
    if (event.type === "session.started") {
      nextData.persistenceStateText = "等待识别";
      nextData.liveText = "";
      nextData.liveCharCount = 0;
    }
    if (Object.keys(nextData).length) {
      this.setData(nextData);
    }
  },

  handlePartial(text) {
    const displayText = normalizeMemoirAsrText(text || "");
    this.setData({
      liveText: displayText,
      liveCharCount: countTextChars(displayText),
      persistenceStateText: "识别中",
      error: "",
      errorKindText: "",
    });
  },

  handleFinal(event) {
    const text = normalizeMemoirAsrText(event.text || "");
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
    this.consecutiveNoAnswerPrompts = 0;
    this.cancelPendingNoAnswerPrompt();
    this.setData({
      liveText: "",
      liveCharCount: 0,
      ...assistantTranscriptState(transcriptLines, {
        guidanceMode: this.data.guidanceMode,
        lastFollowUpSourceMessageId: this.data.lastFollowUpSourceMessageId,
        followUpLoading: false,
        assistantPromptText: "",
        followUpMessageId: "",
        followUpAudioFileId: "",
        followUpTTSStateText: "",
        ttsAlignment: [],
      }),
      persistenceStateText: event.message_id && event.utterance_id && event.transcript_version_id
        ? "已保存"
        : "待同步",
      error: "",
      errorKindText: "",
      assistantPromptText: "",
      dismissedFollowUpMessageId: "",
      followUpMessageId: "",
      followUpAudioFileId: "",
      followUpLoading: false,
      followUpTTSLoading: false,
      followUpTTSError: "",
    });
    writeMemoirVoiceSessionSnapshot(this.data.voiceSessionId, { transcriptLines });
    trackAuditAction(
      "voice_chat.asr_final",
      {
        turn_final: event.turn_final !== false,
        has_audio: Boolean(event.audio_file_id),
      },
      { voiceSessionId: this.data.voiceSessionId },
    );
    if (event.message_id && event.utterance_id) {
      void this.confirmSavedTranscriptSnapshot(event);
    }
    if (event.message_id && event.turn_final !== false) {
      void this.generateFollowUp(false);
    }
  },

  handleError(message, error) {
    if (error?.kind === "connection") {
      this.persistPendingVoiceClose("connection_error");
    }
    const nextData: Partial<VoiceChatData> = {
      error: message,
      errorKindText: errorKindText(error),
      persistenceStateText: error?.kind === "persistence" ? "保存失败" : this.data.persistenceStateText,
      status: "error",
      statusText: statusText("error"),
      recording: false,
      primaryActionText: "重新连接",
      captureStateText: "录音异常",
      recoveryNotice: error?.kind === "connection" ? "语音连接中断，已保留本地讲述记录" : this.data.recoveryNotice,
      canReconnect: error?.kind === "connection",
      canFinishAfterDisconnect: error?.kind === "connection",
    };
    if (error?.state) {
      nextData.gatewayStateText = gatewayStateText(error.state);
    }
    this.setData(nextData);
  },
});
