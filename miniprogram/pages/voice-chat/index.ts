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
  type MemoirDiagnosticValue,
  type MemoirStreamingAsrDiagnosticEvent,
  type VoiceChatConnectionStatus,
} from "../../services/memoir-streaming-asr";
import {
  createMemoirAssistantTTS,
  createMemoirVoiceNoAnswerPrompt,
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
  syncVoiceAuthorizationConsent,
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
  clearMemoirVoiceSessionPendingClose,
  readMemoirVoiceSessionSnapshot,
  transcriptLinesFromVoiceSessionDetail,
  writeMemoirVoiceSessionPendingClose,
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
  finishingSession: boolean;
  recoveryNotice: string;
  canReconnect: boolean;
  canFinishAfterDisconnect: boolean;
  diagnosticSummary: string;
  diagnosticEventCount: number;
}

let streamingClient: MemoirStreamingAsrClient | null = null;
let followUpRequestSerial = 0;
const AUTO_STOP_SILENCE_MS = 1800;
const AUTO_STOP_MIN_RECORDING_MS = 1200;
const AUTO_LISTEN_NO_ANSWER_MS = 15000;
const MAX_CONSECUTIVE_NO_ANSWER_PROMPTS = 2;
const VOICE_CHAT_DIAGNOSTIC_BUILD = "voice-chat-cleanup-2026-06-24";
const MAX_VOICE_DIAGNOSTIC_EVENTS = 140;

interface VoiceDiagnosticEntry {
  at: string;
  elapsedMs: number;
  name: string;
  data?: Record<string, MemoirDiagnosticValue>;
}

interface AutoListenGateSnapshot {
  hasClient: boolean;
  autoListenEnabled: boolean;
  status: VoiceChatConnectionStatus;
  recording: boolean;
  connecting: boolean;
  followUpLoading: boolean;
  pendingAfterPlayback: boolean;
  canStart: boolean;
}

interface ClipboardCapableWx {
  setClipboardData?: (options: {
    data: string;
    success?: () => void;
  }) => void;
  showToast?: typeof wx.showToast;
  getSystemInfoSync?: () => unknown;
}

function formatDiagnosticData(data?: Record<string, MemoirDiagnosticValue>): string {
  if (!data || !Object.keys(data).length) {
    return "";
  }
  return Object.keys(data)
    .sort()
    .map((key) => `${key}=${String(data[key] ?? "")}`)
    .join(" ");
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
  recordVoiceDiagnostic(name: string, data?: Record<string, MemoirDiagnosticValue>): void;
  voiceDiagnosticText(): string;
  copyVoiceDiagnostics(): void;
  autoListenGateSnapshot(): AutoListenGateSnapshot;
  connectGateway(): Promise<void>;
  startRecording(autoTriggered?: boolean): boolean;
  startRecordingAfterPrivacy(autoTriggered?: boolean): Promise<boolean>;
  toggleRecording(): void;
  shouldAutoStartListening(): boolean;
  tryStartAutoListenAfterTTS(): boolean;
  cancelPendingNoAnswerPrompt(clearLoading?: boolean): void;
  handleNoAnswerAutoStop(): Promise<void>;
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
  noAnswerPromptInFlight: boolean;
  noAnswerPromptRequestSerial: number;
  consecutiveNoAnswerPrompts: number;
  handledNoAnswerFrameCount: number;
  autoListenPendingAfterPlayback: boolean;
  voiceDiagnosticStartedAtMs: number;
  voiceDiagnosticEvents: VoiceDiagnosticEntry[];
  lastCaptureDiagnosticState: string;
  lastCaptureDiagnosticFrameCount: number;
}>({
  audio: null,
  ttsPlayToken: 0,
  ttsProgressTimer: null,
  ttsProgressStartedAtMs: 0,
  ttsRequestSerial: 0,
  noAnswerPromptInFlight: false,
  noAnswerPromptRequestSerial: 0,
  consecutiveNoAnswerPrompts: 0,
  handledNoAnswerFrameCount: -1,
  autoListenPendingAfterPlayback: false,
  voiceDiagnosticStartedAtMs: 0,
  voiceDiagnosticEvents: [],
  lastCaptureDiagnosticState: "",
  lastCaptureDiagnosticFrameCount: -1,

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
    finishingSession: false,
    recoveryNotice: "",
    canReconnect: false,
    canFinishAfterDisconnect: false,
    diagnosticSummary: "诊断未开始",
    diagnosticEventCount: 0,
  },

  recordVoiceDiagnostic(name, data = {}) {
    const now = Date.now();
    if (!this.voiceDiagnosticStartedAtMs) {
      this.voiceDiagnosticStartedAtMs = now;
    }
    const event: VoiceDiagnosticEntry = {
      at: new Date(now).toISOString(),
      elapsedMs: now - this.voiceDiagnosticStartedAtMs,
      name,
      data,
    };
    this.voiceDiagnosticEvents.push(event);
    if (this.voiceDiagnosticEvents.length > MAX_VOICE_DIAGNOSTIC_EVENTS) {
      this.voiceDiagnosticEvents = this.voiceDiagnosticEvents.slice(-MAX_VOICE_DIAGNOSTIC_EVENTS);
    }
    const dataText = formatDiagnosticData(data);
    this.setData({
      diagnosticSummary: dataText ? `${name} · ${dataText}` : name,
      diagnosticEventCount: this.voiceDiagnosticEvents.length,
    });
    const wxWithDiagnostics = typeof wx !== "undefined" ? wx as unknown as ClipboardCapableWx : null;
    const shouldLogToConsole = typeof wxWithDiagnostics?.getSystemInfoSync === "function";
    if (shouldLogToConsole && typeof console !== "undefined" && typeof console.info === "function") {
      console.info("[voice-chat-diagnostic]", event);
    }
  },

  voiceDiagnosticText() {
    const header = [
      `build=${VOICE_CHAT_DIAGNOSTIC_BUILD}`,
      `session=${this.data.voiceSessionId || ""}`,
      `mode=${this.data.guidanceMode}/${this.data.currentGuidanceModule || ""}`,
      `status=${this.data.status} connecting=${this.data.connecting} recording=${this.data.recording}`,
      `capture=${this.data.captureStateText} frames=${this.data.captureFrameCount} kb=${this.data.captureKilobytes}`,
      `ttsLoading=${this.data.followUpTTSLoading} ttsPlaying=${this.data.followUpTTSPlaying} ttsState=${this.data.followUpTTSStateText}`,
      `autoListen=${this.data.autoListenEnabled} pendingAfterPlayback=${this.autoListenPendingAfterPlayback} autoText=${this.data.autoListenStateText}`,
      `error=${this.data.error || ""}`,
    ];
    const events = this.voiceDiagnosticEvents.map((event) => {
      const dataText = formatDiagnosticData(event.data);
      return `[+${event.elapsedMs}ms] ${event.name}${dataText ? ` ${dataText}` : ""}`;
    });
    return [...header, "events:", ...events].join("\n");
  },

  copyVoiceDiagnostics() {
    const text = this.voiceDiagnosticText();
    this.recordVoiceDiagnostic("page.diagnostics.copy", { eventCount: this.voiceDiagnosticEvents.length });
    if (typeof wx !== "undefined") {
      const clipboardWx = wx as unknown as ClipboardCapableWx;
      if (typeof clipboardWx.setClipboardData === "function") {
        clipboardWx.setClipboardData({
          data: text,
          success: () => {
            if (typeof clipboardWx.showToast === "function") {
              clipboardWx.showToast({ title: "诊断已复制", icon: "none" });
            }
          },
        });
        return;
      }
    }
    if (typeof console !== "undefined" && typeof console.info === "function") {
      console.info("[voice-chat-diagnostic-text]", text);
    }
  },

  autoListenGateSnapshot() {
    const canStart = Boolean(streamingClient) && canAutoStartListeningAfterTTS({
      autoListenEnabled: this.data.autoListenEnabled,
      status: this.data.status,
      recording: this.data.recording,
      connecting: this.data.connecting,
      followUpLoading: this.data.followUpLoading,
    });
    return {
      hasClient: Boolean(streamingClient),
      autoListenEnabled: this.data.autoListenEnabled,
      status: this.data.status,
      recording: this.data.recording,
      connecting: this.data.connecting,
      followUpLoading: this.data.followUpLoading,
      pendingAfterPlayback: this.autoListenPendingAfterPlayback,
      canStart,
    };
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
    this.recordVoiceDiagnostic("page.onLoad", {
      build: VOICE_CHAT_DIAGNOSTIC_BUILD,
      hasVoiceSessionId: Boolean(voiceSessionId),
      guidanceMode,
      currentGuidanceModule,
      voiceAuthorizationAgreed,
    });
    if (!voiceSessionId) {
      this.recordVoiceDiagnostic("page.onLoad.missingSession");
      this.setData({ error: "缺少语音会话" });
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
    this.recordVoiceDiagnostic("page.onShow", {
      voiceAuthorizationAgreed: this.data.voiceAuthorizationAgreed,
      status: this.data.status,
      hasClient: Boolean(streamingClient),
    });
  },

  onHide() {
    if (this.data.recording && streamingClient) {
      streamingClient.stopRecording();
      this.persistPendingVoiceClose("hide");
      this.setData({
        recording: false,
        primaryActionText: "继续说话",
        persistenceStateText: "正在保存当前句",
        recoveryNotice: "已暂停收音，返回后会恢复已保存的访谈记录",
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
      && this.data.status !== "closed"
      && this.data.status !== "error"
      && !streamingClient
    ) {
      void this.syncVoiceAuthorizationAndConnect();
    }
  },

  async changeVoiceAuthorization(event) {
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
      this.recordVoiceDiagnostic("voiceAuthorization.localMissing");
      this.ensureVoiceAuthorization(false);
      return false;
    }
    try {
      this.recordVoiceDiagnostic("voiceAuthorization.sync.begin");
      await syncVoiceAuthorizationConsent({
        sourceSessionId: this.data.voiceSessionId || undefined,
      });
      this.recordVoiceDiagnostic("voiceAuthorization.sync.success");
      return true;
    } catch (error) {
      const message = error instanceof Error ? error.message : "声纹授权同步失败";
      this.recordVoiceDiagnostic("voiceAuthorization.sync.error", { message });
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
    this.recordVoiceDiagnostic("page.syncAndConnect.begin", { autoTriggered });
    const synced = await this.syncVoiceAuthorizationForSession();
    if (!synced) {
      if (autoTriggered) {
        this.setData({ autoListenStateText: "自动收音已暂停，等待声纹授权同步" });
      }
      this.recordVoiceDiagnostic("page.syncAndConnect.blocked", { autoTriggered });
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
      recoveryNotice: snapshot.pendingClose ? "正在恢复上次离开前的访谈记录" : this.data.recoveryNotice,
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
      recoveryNotice: "上次离开时最后一句可能未保存，请检查转写记录",
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
    const assistantLine = visibleAssistantLine(nextLines, this.data.dismissedFollowUpMessageId);
    const assistantCopy = assistantPanelCopy(nextLines);
    const sessionPatch: Partial<VoiceChatData> = {};
    if (detail.session.status === "stopped") {
      sessionPatch.status = "closed";
      sessionPatch.statusText = statusText("closed");
      sessionPatch.primaryActionText = "已结束";
      sessionPatch.recording = false;
      sessionPatch.persistenceStateText = "已保存";
    }
    if (detail.session.status === "failed") {
      sessionPatch.status = "error";
      sessionPatch.statusText = statusText("error");
      sessionPatch.primaryActionText = "重新开始";
      sessionPatch.recording = false;
      sessionPatch.persistenceStateText = "会话异常";
      sessionPatch.error = detail.session.error_message || "语音会话异常结束，请返回首页重新开始";
    }
    this.setData({
      ...sessionPatch,
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
    streamingClient?.close({ force: true });
    streamingClient = null;
    await this.refreshVoiceSessionSnapshot();
    this.setData({
      recording: false,
      status: "closed",
      statusText: statusText("closed"),
      primaryActionText: "已结束",
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
      this.recordVoiceDiagnostic("tts.play.skip", { reason: "missingAudioFileId" });
      return;
    }
    this.stopFollowUpAudio();
    const playToken = this.ttsPlayToken + 1;
    this.ttsPlayToken = playToken;
    this.recordVoiceDiagnostic("tts.play.begin", {
      autoListenAfterPlayback: Boolean(options.autoListenAfterPlayback),
      hasAudioFileId: Boolean(audioFileId),
      playToken,
    });
    this.setData({
      followUpTTSPlaying: true,
      followUpTTSError: "",
      followUpTTSStateText: "正在播放",
    });
    this.updateFollowUpTTSProgress(0, 0);
    try {
      const tempFilePath = await downloadMemoirVoiceAudio(audioFileId);
      if (playToken !== this.ttsPlayToken) {
        this.recordVoiceDiagnostic("tts.play.download.stale", { playToken, currentToken: this.ttsPlayToken });
        return;
      }
      this.recordVoiceDiagnostic("tts.play.download.success", { hasTempFilePath: Boolean(tempFilePath), playToken });
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
          this.recordVoiceDiagnostic("tts.audio.onPlay", { playToken });
          this.ttsProgressStartedAtMs = Date.now();
          this.startFollowUpProgressTimer(playToken, audio);
        }
      });
      audio.onTimeUpdate(() => {
        refreshPlaybackProgress();
      });
      audio.onEnded(() => {
        if (playToken === this.ttsPlayToken) {
          if (options.autoListenAfterPlayback) {
            this.autoListenPendingAfterPlayback = true;
          }
          this.recordVoiceDiagnostic("tts.audio.onEnded", {
            playToken,
            autoListenAfterPlayback: Boolean(options.autoListenAfterPlayback),
            pendingAfterPlayback: this.autoListenPendingAfterPlayback,
          });
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
        if (playToken === this.ttsPlayToken && options.autoListenAfterPlayback) {
          this.tryStartAutoListenAfterTTS();
        }
      });
      audio.onError(() => {
        if (playToken === this.ttsPlayToken) {
          this.recordVoiceDiagnostic("tts.audio.onError", { playToken });
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
      this.recordVoiceDiagnostic("tts.audio.play.call", { playToken });
      this.startFollowUpProgressTimer(playToken, audio);
    } catch (error) {
      if (playToken === this.ttsPlayToken) {
        this.recordVoiceDiagnostic("tts.play.error", {
          message: error instanceof Error ? error.message : "AI 语音播放失败",
        });
        this.setData({
          followUpTTSPlaying: false,
          followUpTTSError: error instanceof Error ? error.message : "AI 语音播放失败",
          followUpTTSStateText: "播放失败",
        });
      }
    }
  },

  stopFollowUpAudio() {
    this.recordVoiceDiagnostic("tts.stop", {
      previousToken: this.ttsPlayToken,
      hadAudio: Boolean(this.audio),
      pendingAfterPlayback: this.autoListenPendingAfterPlayback,
    });
    this.ttsPlayToken += 1;
    this.autoListenPendingAfterPlayback = false;
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
      this.recordVoiceDiagnostic("gateway.connect.skip", {
        connecting: this.data.connecting,
        hasVoiceSessionId: Boolean(this.data.voiceSessionId),
      });
      return;
    }
    this.recordVoiceDiagnostic("gateway.connect.begin", {
      status: this.data.status,
      pendingAfterPlayback: this.autoListenPendingAfterPlayback,
      hadClient: Boolean(streamingClient),
    });
    this.setData({ connecting: true, error: "" });
    let connected = false;
    let client: MemoirStreamingAsrClient | null = null;
    const isCurrentClient = () => Boolean(client && streamingClient === client);
    try {
      if (streamingClient) {
        this.recordVoiceDiagnostic("gateway.previousClient.close");
        streamingClient.close({ force: true });
      }
      this.recordVoiceDiagnostic("gateway.client.create.begin");
      client = new MemoirStreamingAsrClient({
        voiceSessionId: this.data.voiceSessionId,
        guidanceMode: this.data.guidanceMode,
        currentGuidanceModule: this.data.currentGuidanceModule,
        autoStopSilenceMs: this.data.autoStopEnabled ? AUTO_STOP_SILENCE_MS : 0,
        minAutoStopRecordingMs: AUTO_STOP_MIN_RECORDING_MS,
        callbacks: {
          onStatus: (status) => {
            if (isCurrentClient()) {
              this.handleStatus(status);
              return;
            }
            this.recordVoiceDiagnostic("gateway.stale.status", { status });
          },
          onCaptureStats: (stats) => {
            if (isCurrentClient()) {
              this.handleCaptureStats(stats);
            }
          },
          onDiagnostic: (event: MemoirStreamingAsrDiagnosticEvent) => (
            this.recordVoiceDiagnostic(
              event.name,
              isCurrentClient() ? event.data : { ...(event.data || {}), staleClient: true },
            )
          ),
          onEvent: (event) => {
            if (isCurrentClient()) {
              this.handleGatewayEvent(event);
            }
          },
          onPartial: (text) => {
            if (isCurrentClient()) {
              this.handlePartial(text);
            }
          },
          onFinal: (event) => {
            if (isCurrentClient()) {
              this.handleFinal(event);
            }
          },
          onError: (message, error) => {
            if (isCurrentClient()) {
              this.handleError(message, error);
              return;
            }
            this.recordVoiceDiagnostic("gateway.stale.error", { message });
          },
        },
      });
      streamingClient = client;
      this.recordVoiceDiagnostic("gateway.client.create.success");
      this.recordVoiceDiagnostic("gateway.client.connect.call");
      await client.connect();
      connected = true;
      this.recordVoiceDiagnostic("gateway.connect.success", { status: this.data.status });
    } catch (error) {
      if (client && streamingClient === client) {
        streamingClient = null;
      }
      if (client) {
        try {
          client.close({ force: true });
        } catch (closeError) {
          const closeMessage = closeError instanceof Error ? closeError.message : "语音连接关闭失败";
          this.recordVoiceDiagnostic("gateway.connect.closeAfterError", { message: closeMessage });
        }
      }
      const message = error instanceof Error ? error.message : "语音连接失败";
      this.recordVoiceDiagnostic("gateway.connect.error", { message });
      this.handleError(message, classifyVoiceClientConnectionError(message));
    } finally {
      this.setData({ connecting: false });
      this.recordVoiceDiagnostic("gateway.connect.finally", {
        connected,
        status: this.data.status,
        pendingAfterPlayback: this.autoListenPendingAfterPlayback,
      });
      if (connected) {
        this.tryStartAutoListenAfterTTS();
      }
    }
  },

  startRecording(autoTriggered = false) {
    this.recordVoiceDiagnostic("recording.start.tap", {
      autoTriggered,
      hasClient: Boolean(streamingClient),
      status: this.data.status,
      connecting: this.data.connecting,
      recording: this.data.recording,
    });
    if (!this.ensureVoiceAuthorization(autoTriggered)) {
      this.recordVoiceDiagnostic("recording.start.blocked", { reason: "voiceAuthorization", autoTriggered });
      return false;
    }
    if (!streamingClient || this.data.status === "closed" || this.data.status === "error") {
      this.recordVoiceDiagnostic("recording.start.reconnect", {
        autoTriggered,
        hasClient: Boolean(streamingClient),
        status: this.data.status,
      });
      void this.syncVoiceAuthorizationAndConnect(autoTriggered);
      this.setData({ autoListenStateText: autoTriggered ? "正在重新连接语音" : this.data.autoListenStateText });
      return false;
    }
    void this.startRecordingAfterPrivacy(autoTriggered);
    return true;
  },

  async startRecordingAfterPrivacy(autoTriggered = false) {
    this.recordVoiceDiagnostic("recording.privacy.begin", { autoTriggered });
    if (!autoTriggered) {
      this.consecutiveNoAnswerPrompts = 0;
      this.cancelPendingNoAnswerPrompt();
    }
    if (!(await this.syncVoiceAuthorizationForSession())) {
      this.recordVoiceDiagnostic("recording.privacy.blocked", { reason: "voiceAuthorizationSync", autoTriggered });
      return false;
    }
    try {
      await ensureRecorderPrivacyAuthorization();
      this.recordVoiceDiagnostic("recording.privacy.success", { autoTriggered });
    } catch (error) {
      const message = error instanceof Error ? error.message : "微信隐私授权失败";
      this.recordVoiceDiagnostic("recording.privacy.error", { message, autoTriggered });
      this.handleError(message, { kind: "recorder", code: "wechat_privacy_authorization_failed", message });
      this.setData({
        recording: false,
        primaryActionText: "继续说话",
        autoListenStateText: autoTriggered ? "自动收音已暂停，等待隐私授权" : this.data.autoListenStateText,
      });
      return false;
    }
    if (!streamingClient || this.data.status === "closed" || this.data.status === "error") {
      this.recordVoiceDiagnostic("recording.start.reconnectAfterPrivacy", {
        autoTriggered,
        hasClient: Boolean(streamingClient),
        status: this.data.status,
      });
      void this.syncVoiceAuthorizationAndConnect(autoTriggered);
      this.setData({ autoListenStateText: autoTriggered ? "正在重新连接语音" : this.data.autoListenStateText });
      return false;
    }
    const started = streamingClient.startRecording({
      noVoiceActivityTimeoutMs: autoTriggered ? AUTO_LISTEN_NO_ANSWER_MS : 0,
    });
    this.recordVoiceDiagnostic("recording.client.start.result", { autoTriggered, started });
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
    this.cancelFollowUpTTS();
    this.handledNoAnswerFrameCount = -1;
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
    if (!streamingClient || this.data.status === "closed" || this.data.status === "error") {
      void this.syncVoiceAuthorizationAndConnect(false);
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
    return this.autoListenGateSnapshot().canStart;
  },

  tryStartAutoListenAfterTTS() {
    const gate = this.autoListenGateSnapshot();
    this.recordVoiceDiagnostic("autoListen.tryAfterTTS", { ...gate });
    if (!this.autoListenPendingAfterPlayback || !gate.canStart) {
      return false;
    }
    this.autoListenPendingAfterPlayback = false;
    const started = this.startRecording(true);
    this.recordVoiceDiagnostic("autoListen.startAfterTTS.result", { started });
    return started;
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
      persistenceStateText: "AI 正在确认",
      autoListenStateText: "没听到回答，AI 正在确认",
    });
    try {
      const response = await createMemoirVoiceNoAnswerPrompt(this.data.voiceSessionId, { attempt });
      if (requestSerial !== this.noAnswerPromptRequestSerial) {
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
        persistenceStateText: autoListenAfterPlayback ? "AI 正在确认" : "已暂停收音",
        autoListenStateText: autoListenAfterPlayback ? "AI 确认后会继续听" : "已暂停，可点继续说话",
        canRequestFollowUp: false,
      });
      writeMemoirVoiceSessionSnapshot(this.data.voiceSessionId, { transcriptLines });
      await this.prepareFollowUpTTS(response.message.id, false, autoListenAfterPlayback);
    } catch (error) {
      if (requestSerial !== this.noAnswerPromptRequestSerial) {
        return;
      }
      this.setData({
        followUpError: error instanceof Error ? error.message : "AI 确认生成失败",
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
      primaryActionText: "正在结束",
      persistenceStateText: this.data.recording ? "正在保存最后一句" : "正在结束",
    });
    if (!streamingClient) {
      await this.refreshVoiceSessionSnapshot();
      this.setData({ finishingSession: false });
      return;
    }
    const client = streamingClient;
    try {
      await client.finishSession();
      streamingClient = null;
      this.setData({
        recording: false,
        primaryActionText: "已结束",
        status: "closed",
        statusText: statusText("closed"),
        persistenceStateText: "正在同步",
      });
      await this.refreshVoiceSessionSnapshot();
      this.setData({ persistenceStateText: "已保存" });
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
      await this.refreshVoiceSessionSnapshot();
      this.handleError(error instanceof Error ? error.message : "语音会话结束失败");
    } finally {
      this.setData({ finishingSession: false });
    }
  },

  handleStatus(status) {
    this.recordVoiceDiagnostic("status.update", {
      previousStatus: this.data.status,
      status,
      recording: this.data.recording,
      connecting: this.data.connecting,
    });
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
    const frameCheckpoint = stats.frameCount !== this.lastCaptureDiagnosticFrameCount
      && (stats.frameCount <= 1 || stats.frameCount % 20 === 0);
    const shouldRecordDiagnostic = stats.state !== this.lastCaptureDiagnosticState
      || frameCheckpoint
      || Boolean(stats.autoStopped);
    if (shouldRecordDiagnostic) {
      this.lastCaptureDiagnosticState = stats.state;
      this.lastCaptureDiagnosticFrameCount = stats.frameCount;
      this.recordVoiceDiagnostic("capture.stats", {
        state: stats.state,
        frameCount: stats.frameCount,
        audioBytesSent: stats.audioBytesSent,
        recordingElapsedMs: stats.recordingElapsedMs,
        gatewayState: stats.gatewayState,
        audioLevel: Math.round((stats.audioLevel || 0) * 1000) / 1000,
        autoStopped: stats.autoStopped,
        autoStopReason: stats.autoStopReason,
      });
    }
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
    this.recordVoiceDiagnostic("gateway.event", {
      type: event.type,
      state: event.state,
      error: event.error,
    });
    const nextData: Partial<VoiceChatData> = {};
    if (event.state) {
      nextData.gatewayStateText = gatewayStateText(event.state);
    }
    if (event.type === "audio.ended") {
      nextData.recording = false;
      nextData.primaryActionText = "继续说话";
      nextData.persistenceStateText = this.data.persistenceStateText === "已保存"
        || this.data.persistenceStateText === "未听到回答"
        || this.data.persistenceStateText === "AI 正在确认"
        ? this.data.persistenceStateText
        : "正在保存";
    }
    if (event.type === "session.started") {
      nextData.persistenceStateText = "等待识别";
    }
    if (Object.keys(nextData).length) {
      this.setData(nextData);
    }
  },

  handlePartial(text) {
    this.recordVoiceDiagnostic("asr.partial", { textLength: text.length });
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
      this.recordVoiceDiagnostic("asr.final.skip", { reason: "emptyText" });
      return;
    }
    this.recordVoiceDiagnostic("asr.final", {
      textLength: text.length,
      hasMessageId: Boolean(event.message_id),
      hasUtteranceId: Boolean(event.utterance_id),
      hasAudioFileId: Boolean(event.audio_file_id),
    });
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
    this.consecutiveNoAnswerPrompts = 0;
    this.cancelPendingNoAnswerPrompt();
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
      followUpLoading: false,
      followUpTTSLoading: false,
      followUpTTSError: "",
      followUpTTSStateText: "",
      canRequestFollowUp: canRequestFollowUp(
        transcriptLines,
        this.data.guidanceMode,
        this.data.lastFollowUpSourceMessageId,
        false,
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
    this.recordVoiceDiagnostic("page.error", {
      message,
      kind: error?.kind,
      code: error?.code,
      state: error?.state,
    });
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
      recoveryNotice: error?.kind === "connection" ? "语音连接中断，已保留本地访谈记录" : this.data.recoveryNotice,
      canReconnect: error?.kind === "connection",
      canFinishAfterDisconnect: error?.kind === "connection",
    };
    if (error?.state) {
      nextData.gatewayStateText = gatewayStateText(error.state);
    }
    this.setData(nextData);
  },
});
