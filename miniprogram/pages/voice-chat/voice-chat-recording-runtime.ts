import type {
  MemoirSpeakerRole,
  MemoirVoiceSessionDetail,
  VoiceChatErrorInfo,
  VoiceGatewayServerEvent,
} from "../../domain/memoir-voice-session";
import { returnToStory } from "../../services/navigation";
import { trackAuditAction } from "../../services/audit-tracker";
import { assistantTurnQueueItem } from "../../services/voice-assistant-turn-queue";
import {
  classifyVoiceClientConnectionError,
  type MemoirCaptureStats,
  type MemoirStreamingAsrClientEvent,
  type VoiceChatConnectionStatus,
} from "../../services/memoir-streaming-asr";
import { createMemoirVoiceNoAnswerPrompt } from "../../services/memoir-voice-session-api";
import { StreamingVoiceController } from "../../services/streaming-voice-controller";
import {
  canRequestFollowUp,
  latestPersistedAssistantMessageId,
} from "../../services/voice-chat-state";
import { writeMemoirVoiceSessionSnapshot } from "../../stores/memoir-cache";
import type { VoiceChatAssistantPlaybackMethods } from "./voice-chat-assistant-playback";
import {
  type AutoListenGateSnapshot,
  applyVoiceChatDataPatch,
  bookCompletionRoundFinished,
  type VoiceChatDataContext,
  voiceCaptureStatsPatch,
  voiceChatErrorPatch,
  voiceConnectionStatusPatch,
  voiceGatewayEventPatch,
} from "./voice-chat-page-state";
import {
  finishSessionSavedPatch,
  finishSessionStartPatch,
  noAnswerPromptFailedPatch,
  noAnswerPromptPlan,
  noAnswerPromptQueuedPatch,
  noAnswerPromptStartPatch,
  voiceFinalTranscriptResult,
  voiceAssistantInterruptedPatch,
  voiceClientUnavailableForRecording,
  voiceLiveTranscriptPatch,
  voiceRecordingReconnectPatch,
  voiceRecordingStartRequestedPatch,
  voiceRecordingStartedPatch,
  voiceRecordingStoppedPatch,
  voiceRecordingUnavailablePatch,
} from "./voice-chat-recording-flow";

export const AUTO_STOP_SILENCE_MS = 3500;
const AUTO_STOP_MIN_RECORDING_MS = 2000;
const AUTO_LISTEN_NO_ANSWER_MS = 15000;
const MAX_CONSECUTIVE_NO_ANSWER_PROMPTS = 2;

export interface VoiceChatRecordingRuntimeMethods {
  connectGateway(): Promise<boolean>;
  performConnectGateway(): Promise<boolean>;
  startRecording(autoTriggered?: boolean, speakerRole?: MemoirSpeakerRole): boolean;
  startRecordingAfterPrivacy(
    autoTriggered?: boolean,
    speakerRole?: MemoirSpeakerRole,
    requestedGeneration?: number,
  ): Promise<boolean>;
  toggleRecording(): void;
  shouldAutoStartListening(): boolean;
  tryStartAutoListenAfterTTS(): Promise<boolean>;
  cancelPendingNoAnswerPrompt(clearLoading?: boolean): void;
  handleNoAnswerAutoStop(): Promise<void>;
  finishSession(): Promise<void>;
  handleStatus(status: VoiceChatConnectionStatus): void;
  handleCaptureStats(stats: MemoirCaptureStats): void;
  handleGatewayEvent(event: VoiceGatewayServerEvent): void;
  handleClientEvent(event: MemoirStreamingAsrClientEvent): void;
  handlePartial(text: string): void;
  handleFinal(event: VoiceGatewayServerEvent): void;
  handleError(message: string, error?: VoiceChatErrorInfo): void;
}

export interface VoiceChatRecordingRuntimeHost extends VoiceChatDataContext {
  voiceStreaming: StreamingVoiceController;
  gatewayConnectPromise: Promise<boolean> | null;
  recordingStartPromise: Promise<boolean> | null;
  recordingStartGeneration: number;
  pageSuspended: boolean;
  followUpRequestSerial: number;
  consecutiveNoAnswerPrompts: number;
  handledNoAnswerFrameCount: number;
  autoListenPendingAfterPlayback: boolean;
  noAnswerPromptInFlight: boolean;
  noAnswerPromptRequestSerial: number;
  ensureVoiceAuthorization(autoTriggered?: boolean): boolean;
  syncVoiceAuthorizationForSession(forRecording?: boolean): Promise<boolean>;
  autoListenGateSnapshot(): AutoListenGateSnapshot;
  refreshVoiceSessionSnapshot(): Promise<MemoirVoiceSessionDetail | null>;
  confirmSavedTranscriptSnapshot(event: VoiceGatewayServerEvent): Promise<void>;
  startCompletionIssueVerification(): void;
  stopCompletionIssueRefresh(): void;
  generateFollowUp(regenerate?: boolean): Promise<void>;
  persistPendingVoiceClose(reason: "hide" | "unload" | "connection_error"): void;
  startStoryGrowthWatch(): void;
}

type VoiceChatRecordingRuntimeContext = VoiceChatRecordingRuntimeHost
  & VoiceChatRecordingRuntimeMethods
  & VoiceChatAssistantPlaybackMethods;

export const voiceChatRecordingRuntimeMethods: VoiceChatRecordingRuntimeMethods
  & ThisType<VoiceChatRecordingRuntimeContext> = {
  connectGateway(): Promise<boolean> {
    if (!this.data.voiceSessionId) {
      return Promise.resolve(false);
    }
    if (this.gatewayConnectPromise) {
      return this.gatewayConnectPromise;
    }
    const connectPromise = this.performConnectGateway().catch((error) => {
      const message = error instanceof Error ? error.message : "语音连接失败";
      this.handleError(message, classifyVoiceClientConnectionError(message));
      return false;
    });
    this.gatewayConnectPromise = connectPromise;
    void connectPromise.finally(() => {
      if (this.gatewayConnectPromise === connectPromise) {
        this.gatewayConnectPromise = null;
      }
    });
    return connectPromise;
  },

  async performConnectGateway(): Promise<boolean> {
    applyVoiceChatDataPatch(this, { connecting: true, error: "" });
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
          onClientEvent: (event) => {
            this.handleClientEvent(event);
          },
        },
      });
      connected = true;
    } catch (error) {
      const message = error instanceof Error ? error.message : "语音连接失败";
      this.handleError(message, classifyVoiceClientConnectionError(message));
    } finally {
      applyVoiceChatDataPatch(this, { connecting: false });
      if (connected) {
        await this.tryStartAutoListenAfterTTS();
      }
    }
    return connected;
  },

  startRecording(autoTriggered: boolean = false, speakerRole?: MemoirSpeakerRole) {
    if (
      this.data.finishingSession
      || bookCompletionRoundFinished(this.data)
      || !this.ensureVoiceAuthorization(autoTriggered)
    ) {
      return false;
    }
    if (this.recordingStartPromise) {
      return true;
    }
    applyVoiceChatDataPatch(this, voiceRecordingStartRequestedPatch());
    const requestGeneration = ++this.recordingStartGeneration;
    const startPromise = this.startRecordingAfterPrivacy(
      autoTriggered,
      speakerRole,
      requestGeneration,
    ).catch((error) => {
      const message = error instanceof Error ? error.message : "录音启动失败";
      this.handleError(message, { kind: "recorder", code: "recorder_start_failed", message });
      return false;
    });
    this.recordingStartPromise = startPromise;
    void startPromise.finally(() => {
      if (this.recordingStartPromise === startPromise) {
        this.recordingStartPromise = null;
        applyVoiceChatDataPatch(this, { recordingStarting: false });
      }
    });
    return true;
  },

  async startRecordingAfterPrivacy(
    autoTriggered: boolean = false,
    speakerRole?: MemoirSpeakerRole,
    requestedGeneration?: number,
  ) {
    const requestGeneration = requestedGeneration ?? ++this.recordingStartGeneration;
    const interruptingAssistant = !autoTriggered && this.assistantTurnActiveForBargeIn();
    const parentMessageId = this.data.interviewType === "human_interview"
      ? ""
      : latestPersistedAssistantMessageId(this.data.transcriptLines);
    if (!autoTriggered) {
      this.consecutiveNoAnswerPrompts = 0;
      this.cancelPendingNoAnswerPrompt(!interruptingAssistant);
    }
    if (interruptingAssistant) {
      this.followUpRequestSerial += 1;
      this.cancelFollowUpTTS("interrupted_by_user");
      applyVoiceChatDataPatch(this, voiceAssistantInterruptedPatch());
    }
    await this.stopAssistantBargeInMonitor();
    if (
      requestGeneration !== this.recordingStartGeneration
      || this.pageSuspended
      || this.data.finishingSession
      || bookCompletionRoundFinished(this.data)
    ) {
      return false;
    }
    if (!(await this.syncVoiceAuthorizationForSession(true))) {
      return false;
    }
    if (
      requestGeneration !== this.recordingStartGeneration
      || this.pageSuspended
      || this.data.finishingSession
      || bookCompletionRoundFinished(this.data)
    ) {
      return false;
    }
    if (
      this.data.connecting
      || voiceClientUnavailableForRecording(this.voiceStreaming.hasClient(), this.data.status)
    ) {
      applyVoiceChatDataPatch(this, voiceRecordingReconnectPatch(autoTriggered, this.data.autoListenStateText));
      const connected = await this.connectGateway();
      if (
        !connected
        || requestGeneration !== this.recordingStartGeneration
        || this.pageSuspended
        || this.data.finishingSession
        || bookCompletionRoundFinished(this.data)
        || voiceClientUnavailableForRecording(this.voiceStreaming.hasClient(), this.data.status)
      ) {
        applyVoiceChatDataPatch(this, voiceRecordingUnavailablePatch(autoTriggered, this.data.autoListenStateText));
        return false;
      }
    }
    if (
      requestGeneration !== this.recordingStartGeneration
      || this.pageSuspended
      || this.data.finishingSession
      || bookCompletionRoundFinished(this.data)
    ) {
      return false;
    }
    const recordingOptions: {
      noVoiceActivityTimeoutMs: number;
      speakerRole?: MemoirSpeakerRole;
      parentMessageId?: string;
    } = {
      noVoiceActivityTimeoutMs: autoTriggered ? AUTO_LISTEN_NO_ANSWER_MS : 0,
    };
    if (this.data.interviewType === "human_interview") {
      recordingOptions.speakerRole = speakerRole || this.data.speakerRole;
    } else if (parentMessageId) {
      recordingOptions.parentMessageId = parentMessageId;
    }
    const started = this.voiceStreaming.startRecording(recordingOptions);
    if (!started) {
      applyVoiceChatDataPatch(this, voiceRecordingUnavailablePatch(autoTriggered, this.data.autoListenStateText));
      return false;
    }
    if (!interruptingAssistant) {
      this.cancelFollowUpTTS();
    }
    this.handledNoAnswerFrameCount = -1;
    applyVoiceChatDataPatch(this, voiceRecordingStartedPatch(autoTriggered));
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
    if (this.data.recording) {
      this.voiceStreaming.stopRecording();
      applyVoiceChatDataPatch(this, voiceRecordingStoppedPatch());
      return;
    }
    this.startRecording(false);
  },

  shouldAutoStartListening() {
    if (this.data.interviewType === "human_interview") {
      return false;
    }
    return this.autoListenGateSnapshot().canStart;
  },

  async tryStartAutoListenAfterTTS() {
    if (this.data.interviewType === "human_interview" || this.pageSuspended) {
      return false;
    }
    const gate = this.autoListenGateSnapshot();
    if (!this.autoListenPendingAfterPlayback || !gate.canStart) {
      return false;
    }
    this.autoListenPendingAfterPlayback = false;
    return this.startRecordingAfterPrivacy(true);
  },

  cancelPendingNoAnswerPrompt(clearLoading: boolean = true) {
    this.noAnswerPromptRequestSerial += 1;
    if (!this.noAnswerPromptInFlight) {
      return;
    }
    this.noAnswerPromptInFlight = false;
    if (clearLoading) {
      applyVoiceChatDataPatch(this, { followUpLoading: false });
    }
  },

  async handleNoAnswerAutoStop() {
    if (this.data.interviewType === "human_interview") {
      return;
    }
    if (this.noAnswerPromptInFlight || !this.data.voiceSessionId) {
      return;
    }
    this.noAnswerPromptInFlight = true;
    const requestSerial = this.noAnswerPromptRequestSerial + 1;
    this.noAnswerPromptRequestSerial = requestSerial;
    const { attempt, autoListenAfterPlayback } = noAnswerPromptPlan(
      this.consecutiveNoAnswerPrompts,
      MAX_CONSECUTIVE_NO_ANSWER_PROMPTS,
    );
    this.consecutiveNoAnswerPrompts = attempt;
    applyVoiceChatDataPatch(this, noAnswerPromptStartPatch());
    try {
      const response = await createMemoirVoiceNoAnswerPrompt(this.data.voiceSessionId, { attempt });
      if (requestSerial !== this.noAnswerPromptRequestSerial) {
        return;
      }
      this.enqueueAssistantTurn(assistantTurnQueueItem(response, { autoListenAfterPlayback }));
      applyVoiceChatDataPatch(this, noAnswerPromptQueuedPatch(autoListenAfterPlayback));
    } catch (error) {
      if (requestSerial !== this.noAnswerPromptRequestSerial) {
        return;
      }
      applyVoiceChatDataPatch(this, noAnswerPromptFailedPatch(
        canRequestFollowUp(
          this.data.transcriptLines,
          this.data.guidanceMode,
          this.data.lastFollowUpSourceMessageId,
          false,
        ),
        error instanceof Error ? error.message : "小余确认生成失败",
      ));
    } finally {
      if (requestSerial === this.noAnswerPromptRequestSerial) {
        this.noAnswerPromptInFlight = false;
        applyVoiceChatDataPatch(this, { followUpLoading: false });
      }
    }
  },

  async finishSession() {
    if (this.data.finishingSession) {
      return;
    }
    this.recordingStartGeneration += 1;
    this.stopCompletionIssueRefresh();
    this.cancelFollowUpTTS();
    applyVoiceChatDataPatch(this, finishSessionStartPatch(this.data.recording));
    try {
      if (this.voiceStreaming.hasClient()) await this.voiceStreaming.finishSession();
      const saved = await this.refreshVoiceSessionSnapshot();
      if (saved?.session.status !== "stopped") {
        this.handleError("暂未确认保存结果，请留在此页稍后重试。");
        return;
      }
      trackAuditAction("voice_chat.finish", {}, { voiceSessionId: this.data.voiceSessionId });
      applyVoiceChatDataPatch(this, finishSessionSavedPatch());
      applyVoiceChatDataPatch(this, { persistenceStateText: "已保存" });
      if (this.data.sessionGoal === "book_completion") {
        wx.showModal({
          title: "本次补充已保存",
          content: "回答已经直接放回相关小章节。返回后重新整理本章，就能生成更新后的正文。",
          confirmText: "返回本章",
          showCancel: false,
          success: () => {
            wx.navigateBack({ delta: 1 });
          },
        });
        return;
      }
      if (!this.pageSuspended) returnToStory(this.data.projectId, saved.utterances.length > 0);
    } catch (error) {
      await this.refreshVoiceSessionSnapshot();
      this.handleError(error instanceof Error ? error.message : "语音保存失败");
    } finally {
      applyVoiceChatDataPatch(this, { finishingSession: false });
    }
  },

  handleStatus(status: VoiceChatConnectionStatus) {
    applyVoiceChatDataPatch(this, voiceConnectionStatusPatch(status, this.data));
  },

  handleCaptureStats(stats: MemoirCaptureStats) {
    const noAnswerAutoStopped = stats.autoStopped && stats.autoStopReason === "no_voice";
    const noAnswerPromptAlreadyHandled = noAnswerAutoStopped
      && this.handledNoAnswerFrameCount === stats.frameCount;
    const { patch, shouldHandleNoAnswerAutoStop } = voiceCaptureStatsPatch(stats, {
      noAnswerPromptAlreadyHandled,
    });
    applyVoiceChatDataPatch(this, patch);
    if (shouldHandleNoAnswerAutoStop && !this.pageSuspended) {
      this.handledNoAnswerFrameCount = stats.frameCount;
      void this.handleNoAnswerAutoStop();
    }
  },

  handleGatewayEvent(event: VoiceGatewayServerEvent) {
    const nextData = voiceGatewayEventPatch(event, this.data);
    if (Object.keys(nextData).length) {
      applyVoiceChatDataPatch(this, nextData);
    }
  },

  handleClientEvent(event: MemoirStreamingAsrClientEvent) {
    trackAuditAction(
      "voice_chat.client_event",
      {
        type: event.type,
        message: voiceAuditText(event.message || ""),
        native_message: voiceAuditText(event.nativeMessage || ""),
        gateway_event_type: event.gatewayEventType || "",
        timeout_ms: event.timeoutMs || 0,
        frame_count: event.frameCount,
        audio_bytes_sent: event.audioBytesSent,
        recording_elapsed_ms: event.recordingElapsedMs,
        gateway_state: event.gatewayState || "",
        auto_stopped: event.autoStopped,
        auto_stop_reason: event.autoStopReason || "",
        error_kind: event.errorKind || "",
        error_code: event.errorCode || "",
      },
      { voiceSessionId: this.data.voiceSessionId },
    );
  },

  handlePartial(text: string) {
    applyVoiceChatDataPatch(this, voiceLiveTranscriptPatch(text, this.data.liveScrollTop));
  },

  handleFinal(event: VoiceGatewayServerEvent) {
    const result = voiceFinalTranscriptResult(
      event,
      {
        transcriptLines: this.data.transcriptLines,
        guidanceMode: this.data.guidanceMode,
        lastFollowUpSourceMessageId: this.data.lastFollowUpSourceMessageId,
      },
      `${Date.now()}-${this.data.transcriptLines.length}`,
    );
    if (!result) {
      return;
    }
    this.consecutiveNoAnswerPrompts = 0;
    this.cancelPendingNoAnswerPrompt();
    const completedBookAnswer = this.data.sessionGoal === "book_completion"
      && result.line.role === "user";
    if (completedBookAnswer) {
      this.autoListenPendingAfterPlayback = false;
    }
    applyVoiceChatDataPatch(this, {
      ...result.patch,
      ...(completedBookAnswer ? { assistantExpectsAnswer: false } : {}),
    });
    writeMemoirVoiceSessionSnapshot(this.data.voiceSessionId, { transcriptLines: result.transcriptLines });
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
    const finalSpeakerRole = event.speaker_role
      || (this.data.interviewType === "human_interview" ? this.data.speakerRole : "narrator");
    if (finalSpeakerRole !== "interviewer") {
      this.startCompletionIssueVerification();
    }
    if (
      this.data.interviewType !== "human_interview"
      && event.message_id
      && event.turn_final !== false
      && !this.pageSuspended
    ) {
      void this.generateFollowUp(false);
    }
  },

  handleError(message: string, error?: VoiceChatErrorInfo) {
    trackAuditAction(
      "voice_chat.error",
      {
        message: voiceAuditText(message),
        error_kind: error?.kind || "",
        error_code: error?.code || "",
        gateway_state: error?.state || "",
      },
      { voiceSessionId: this.data.voiceSessionId },
    );
    if (error?.kind === "connection") {
      this.persistPendingVoiceClose("connection_error");
    }
    applyVoiceChatDataPatch(this, voiceChatErrorPatch(message, error, this.data));
  },
};

function voiceAuditText(value: string): string {
  return (value || "").slice(0, 240);
}
