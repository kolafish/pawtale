import type {
  MemoirFollowupCannotRecallResolution,
  MemoirGuidanceMode,
  MemoirInterviewType,
  MemoirSessionGoal,
  MemoirSpeakerRole,
  MemoirVoiceSessionDetail,
  VoiceChatErrorInfo,
  TTSAlignmentSegment,
  VoiceChatTranscriptLine,
  VoiceGatewayServerEvent,
} from "../../domain/voice-chat";
import { definePage } from "../../services/page-definition";
import { trackAuditAction } from "../../services/audit-tracker";
import { AudioPlaybackController } from "../../services/audio-playback";
import { readAccessibilitySettings } from "../../services/accessibility-settings";
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
import {
  classifyVoiceClientConnectionError,
  type MemoirCaptureStats,
  type MemoirStreamingAsrClientEvent,
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
import { readChatTTSSettings } from "../../services/tts-settings";
import { canAutoStartListeningAfterTTS } from "../../services/voice-chat-auto-listen";
import type { StreamingSpeechPlaybackFrameEvent } from "../../services/streaming-speech-playback";
import {
  streamingProgressPercent,
  VoiceAssistantTTSProgressRuntime,
  type VoiceAssistantTTSProgressPatch,
} from "../../services/voice-assistant-tts-progress";
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
  followUpSourceMessageId,
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
  type VoiceChatData,
  initialVoiceChatData,
  voiceChatViewPatch,
  voiceAuthorizationChangePatch,
  voiceAuthorizationConsentPatch,
  voiceAuthorizationRequiredPatch,
  voiceAuthorizationSyncFailedPatch,
  voiceAuthorizationWaitingPatch,
  voiceCaptureStatsPatch,
  voiceChatErrorPatch,
  voiceChatAutoListenGateSnapshot,
  voiceChatSessionEntryPatch,
  voiceConnectionStatusPatch,
  voiceGatewayEventPatch,
  voiceSessionDetailPatch,
  voiceSessionSnapshotPatch,
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
  voiceRecorderPrivacyDeniedPatch,
  voiceRecordingReconnectPatch,
  voiceRecordingStartRequestedPatch,
  voiceRecordingStartedPatch,
  voiceRecordingStoppedPatch,
  voiceRecordingUnavailablePatch,
} from "./voice-chat-recording-flow";
import { VoiceChatCompletionTaskRuntime } from "./voice-chat-completion-task-runtime";
import {
  datasetString,
  queryChoice,
  queryParam,
  type PageDatasetEvent,
  type PageQuery,
} from "../../services/page-input";

const VOICE_AUTHORIZATION_VALUE = "voice_authorization";

interface CheckboxChangeEvent {
  detail: {
    value: string[];
  };
}

interface OpeningTopicDataset {
  promptText?: string;
}

type OpeningTopicTapEvent = PageDatasetEvent<OpeningTopicDataset>;
type SpeakerRoleTapEvent = PageDatasetEvent<{ role?: string }>;

let followUpRequestSerial = 0;
const AUTO_STOP_SILENCE_MS = 3500;
const AUTO_STOP_MIN_RECORDING_MS = 2000;
const AUTO_LISTEN_NO_ANSWER_MS = 15000;
const MAX_CONSECUTIVE_NO_ANSWER_PROMPTS = 2;
const GUIDANCE_MODE_VALUES: readonly MemoirGuidanceMode[] = ["free_narration", "guided_interview"];
const INTERVIEW_TYPE_VALUES: readonly MemoirInterviewType[] = ["ai_interview", "human_interview"];
const SESSION_GOAL_VALUES: readonly MemoirSessionGoal[] = ["open_narration", "book_completion"];
const SPEAKER_ROLE_VALUES: readonly MemoirSpeakerRole[] = ["narrator", "interviewer"];

interface VoiceChatDataContext {
  data: VoiceChatData;
  setData(patch: Partial<VoiceChatData>): void;
}

function setVoiceChatData(context: VoiceChatDataContext, patch: Partial<VoiceChatData>): void {
  const nextData: VoiceChatData = { ...context.data, ...patch };
  context.setData({
    ...patch,
    ...voiceChatViewPatch(nextData),
  });
}

definePage<VoiceChatData>()({
  audioPlayback: new AudioPlaybackController(),
  voiceStreaming: new StreamingVoiceController(),
  assistantBargeInMonitor: new VoiceBargeInMonitor(),
  assistantTurnRuntime: null as VoiceAssistantTurnRuntime | null,
  assistantTurnQueue: [] as AssistantTurnQueueItem[],
  assistantTurnQueueRunning: false,
  assistantAudioPrepareCount: 0,
  assistantStreamLineId: "",
  ttsPlayToken: 0,
  ttsProgress: new VoiceAssistantTTSProgressRuntime(),
  ttsRequestSerial: 0,
  noAnswerPromptInFlight: false,
  noAnswerPromptRequestSerial: 0,
  consecutiveNoAnswerPrompts: 0,
  handledNoAnswerFrameCount: -1,
  autoListenPendingAfterPlayback: false,
  pageSuspended: false,
  gatewayConnectPromise: null as Promise<boolean> | null,
  recordingStartPromise: null as Promise<boolean> | null,
  recordingStartGeneration: 0,
  completionTaskRuntime: null as VoiceChatCompletionTaskRuntime | null,

  data: initialVoiceChatData(),

  completionTasks() {
    if (!this.completionTaskRuntime) {
      this.completionTaskRuntime = new VoiceChatCompletionTaskRuntime({
        readState: () => this.data,
        applyPatch: (patch) => setVoiceChatData(this, patch),
        isSuspended: () => this.pageSuspended,
        cancelAssistant: (reason) => this.cancelFollowUpTTS(reason),
        requestAssistant: () => {
          void this.generateFollowUp(true);
        },
      });
    }
    return this.completionTaskRuntime;
  },

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

  async onLoad(query?: PageQuery) {
    this.refreshAccessibilitySettings();
    const voiceSessionId = queryParam(query, "voiceSessionId");
    const guidanceMode = queryChoice(query, "mode", GUIDANCE_MODE_VALUES, "free_narration");
    const interviewType = queryChoice(query, "interviewType", INTERVIEW_TYPE_VALUES, "ai_interview");
    const sessionGoal = queryChoice(query, "sessionGoal", SESSION_GOAL_VALUES, "open_narration");
    const currentGuidanceModule = queryParam(query, "module");
    const projectTitle = queryParam(query, "projectTitle");
    const voiceAuthorizationAgreed = readVoiceAuthorizationConsent();
    setVoiceChatData(this, voiceChatSessionEntryPatch({
      voiceSessionId,
      projectTitle,
      guidanceMode,
      currentGuidanceModule,
      interviewType,
      sessionGoal,
      voiceAuthorizationAgreed,
    }));
    if (!voiceSessionId) {
      setVoiceChatData(this, { error: "缺少本次语音记录" });
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
      setVoiceChatData(this, voiceAuthorizationWaitingPatch());
    }
  },

  onShow() {
    const wasSuspended = this.pageSuspended;
    this.pageSuspended = false;
    this.refreshAccessibilitySettings();
    this.refreshVoiceAuthorizationConsent();
    if (
      wasSuspended
      && this.data.voiceSessionId
      && !this.data.connecting
      && this.data.status !== "closed"
      && this.data.status !== "error"
    ) {
      void this.refreshVoiceSessionSnapshot();
    }
  },

  refreshAccessibilitySettings() {
    const settings = readAccessibilitySettings();
    setVoiceChatData(this, {
      largeTextEnabled: settings.largeTextEnabled,
      autoBargeInEnabled: settings.autoBargeInEnabled,
      ...(settings.autoBargeInEnabled ? {} : { autoListenStateText: "可以点按钮回答" }),
    });
    if (!settings.autoBargeInEnabled) {
      void this.stopAssistantBargeInMonitor();
    }
  },

  onHide() {
    this.suspendVoiceChat("hide");
  },

  onUnload() {
    this.suspendVoiceChat("unload");
    this.voiceStreaming.close();
  },

  suspendVoiceChat(reason: "hide" | "unload") {
    this.pageSuspended = true;
    this.stopCompletionTaskRefresh();
    this.recordingStartGeneration += 1;
    followUpRequestSerial += 1;
    this.noAnswerPromptRequestSerial += 1;
    this.noAnswerPromptInFlight = false;
    this.persistCurrentTranscriptSnapshot();
    this.persistPendingVoiceClose(reason);
    this.cancelFollowUpTTS(reason === "hide" ? "page_hidden" : "page_unloaded");
    void this.stopAssistantBargeInMonitor();
    if (this.data.recordingStarting) {
      setVoiceChatData(this, { recordingStarting: false });
    }
    if (this.data.recording && this.voiceStreaming.hasClient()) {
      this.voiceStreaming.stopRecording();
      setVoiceChatData(this, {
        recording: false,
        primaryActionText: "继续说话",
        persistenceStateText: "正在保存当前句",
        recoveryNotice: "已暂停收音，返回后会恢复已保存的讲述记录",
      });
    }
  },

  refreshVoiceAuthorizationConsent() {
    const voiceAuthorizationAgreed = readVoiceAuthorizationConsent();
    setVoiceChatData(this, voiceAuthorizationConsentPatch(this.data, voiceAuthorizationAgreed));
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

  async changeVoiceAuthorization(event: CheckboxChangeEvent) {
    const agreed = event.detail.value.includes(VOICE_AUTHORIZATION_VALUE);
    const consent = saveVoiceAuthorizationConsent(agreed);
    if (!agreed && this.voiceStreaming.hasClient() && this.data.recording) {
      this.voiceStreaming.stopRecording();
    }
    setVoiceChatData(this, voiceAuthorizationChangePatch(this.data, consent.agreed));
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

  ensureVoiceAuthorization(autoTriggered: boolean = false) {
    const latestConsent = readVoiceAuthorizationConsent();
    if (latestConsent) {
      if (!this.data.voiceAuthorizationAgreed) {
        setVoiceChatData(this, { voiceAuthorizationAgreed: true, error: "" });
      }
      return true;
    }
    setVoiceChatData(this, voiceAuthorizationRequiredPatch(autoTriggered));
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
      setVoiceChatData(this, voiceAuthorizationSyncFailedPatch(message));
      if (typeof wx !== "undefined" && typeof wx.showToast === "function") {
        wx.showToast({ title: "声纹授权同步失败", icon: "none" });
      }
      return false;
    }
  },

  async syncVoiceAuthorizationAndConnect(autoTriggered: boolean = false) {
    const synced = await this.syncVoiceAuthorizationForSession();
    if (!synced) {
      if (autoTriggered) {
        setVoiceChatData(this, { autoListenStateText: "自动收音已暂停，等待声纹授权同步" });
      }
      return false;
    }
    return this.connectGateway();
  },

  hydrateVoiceSessionSnapshot() {
    const snapshot = readMemoirVoiceSessionSnapshot(this.data.voiceSessionId);
    if (!snapshot.hasSession) {
      return;
    }
    setVoiceChatData(this, voiceSessionSnapshotPatch(snapshot, this.data));
  },

  async refreshVoiceSessionSnapshot() {
    if (!this.data.voiceSessionId) {
      return null;
    }
    try {
      const detail = await getMemoirVoiceSession(this.data.voiceSessionId);
      writeMemoirVoiceSessionSnapshot(this.data.voiceSessionId, { detail });
      this.applyVoiceSessionDetail(detail);
      await this.refreshCompletionTask();
      this.reconcilePendingClose(detail);
      if (detail.session.status !== "stopped" && detail.session.status !== "failed") {
        this.maybeGenerateOpeningFollowUp();
      }
      return detail;
    } catch (error) {
      console.warn("refresh voice session snapshot failed", error);
      const message = error instanceof Error ? error.message : "语音会话加载失败";
      setVoiceChatData(this, {
        status: "error",
        statusText: statusText("error"),
        recording: false,
        connecting: false,
        error: message,
        persistenceStateText: "会话加载失败",
        primaryActionText: "重新进入",
        recoveryNotice: "",
        canReconnect: false,
        canFinishAfterDisconnect: false,
      });
      return null;
    }
  },

  reconcilePendingClose(detail: MemoirVoiceSessionDetail) {
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
      setVoiceChatData(this, {
        recoveryNotice: "",
        canReconnect: false,
        canFinishAfterDisconnect: false,
      });
      return;
    }
    setVoiceChatData(this, {
      recoveryNotice: "上次离开时最后一句可能未保存，请检查讲述记录",
      persistenceStateText: "已恢复已保存记录",
      canReconnect: true,
      canFinishAfterDisconnect: true,
    });
  },

  async confirmSavedTranscriptSnapshot(event: VoiceGatewayServerEvent) {
    if (!this.data.voiceSessionId || !event.message_id || !event.utterance_id) {
      return;
    }
    try {
      const detail = await getMemoirVoiceSession(this.data.voiceSessionId);
      writeMemoirVoiceSessionSnapshot(this.data.voiceSessionId, { detail });
      this.applyVoiceSessionDetail(detail);
      const persisted = detail.messages.some((message) => message.id === event.message_id)
        && detail.utterances.some((utterance) => utterance.id === event.utterance_id);
      setVoiceChatData(this, {
        persistenceStateText: persisted ? "已保存" : "已识别，记录同步中",
      });
    } catch (error) {
      console.warn("confirm saved voice transcript failed", error);
      setVoiceChatData(this, { persistenceStateText: "已保存，本地已缓存" });
    }
  },

  applyVoiceSessionDetail(detail: MemoirVoiceSessionDetail) {
    setVoiceChatData(this, voiceSessionDetailPatch(detail, this.data));
  },

  async refreshCompletionTask(silent = false) {
    return this.completionTasks().refresh(silent);
  },

  stopCompletionTaskRefresh() {
    this.completionTaskRuntime?.stop();
  },

  startCompletionTaskVerification() {
    this.completionTasks().startVerification();
  },

  async switchCompletionTask(note = "用户选择换一个补访问题") {
    await this.completionTasks().switchTask(note);
  },

  async deferCompletionTask() {
    await this.completionTasks().deferTask();
  },

  chooseCannotRecallResolution() {
    if (this.data.completionTaskAction) {
      return;
    }
    wx.showActionSheet({
      itemList: ["接受暂时不完整", "问过家人以后再补"],
      success: (result) => {
        const resolution: MemoirFollowupCannotRecallResolution = result.tapIndex === 0
          ? "accept_incomplete"
          : "ask_family_later";
        void this.applyCannotRecallResolution(resolution);
      },
    });
  },

  async applyCannotRecallResolution(resolution: MemoirFollowupCannotRecallResolution) {
    await this.completionTasks().applyCannotRecall(resolution);
  },

  confirmExcludeCompletionTask() {
    if (this.data.completionTaskAction) {
      return;
    }
    wx.showModal({
      title: "不把这项写进书里？",
      content: "确认后，与这个问题关联的故事和事实将不再用于成书。",
      confirmText: "不收录",
      confirmColor: "#a64b3e",
      success: (result) => {
        if (result.confirm) {
          void this.excludeCompletionTask();
        }
      },
    });
  },

  async excludeCompletionTask() {
    await this.completionTasks().excludeTask();
  },

  async continueToNextCompletionTask() {
    await this.switchCompletionTask("当前补访问题已处理，用户继续下一项");
  },

  persistCurrentTranscriptSnapshot() {
    if (!this.data.voiceSessionId) {
      return;
    }
    writeMemoirVoiceSessionSnapshot(this.data.voiceSessionId, {
      transcriptLines: this.data.transcriptLines,
    });
  },

  persistPendingVoiceClose(reason: "hide" | "unload" | "connection_error") {
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
    setVoiceChatData(this, {
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
    setVoiceChatData(this, {
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
    if (
      this.data.interviewType === "human_interview"
      || this.pageSuspended
      || this.data.followUpLoading
      || (
        this.data.sessionGoal === "book_completion"
        && this.data.completionTask?.status !== "in_progress"
        && this.data.completionTask?.status !== "pending"
      )
    ) {
      return;
    }
    const hasAnyLine = this.data.transcriptLines.some((line) => line.text.trim());
    if (!hasAnyLine) {
      void this.generateFollowUp(false);
    }
  },

  async generateFollowUp(regenerate: boolean = false) {
    if (
      this.data.interviewType === "human_interview"
      || this.pageSuspended
      || !this.data.voiceSessionId
      || this.data.followUpLoading
      || (
        this.data.sessionGoal === "book_completion"
        && this.data.completionTask?.status !== "in_progress"
        && this.data.completionTask?.status !== "pending"
      )
    ) {
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
    this.ttsProgress.resetStreamingAudio();
    setVoiceChatData(this, {
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
      selectedOpeningTopicText: "",
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
        setVoiceChatData(this, {
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

  async generateStreamingFollowUp(sourceMessageId: string, regenerate: boolean, requestSerial: number) {
    const runtime = new VoiceAssistantTurnRuntime(this.data.voiceSessionId, {
      onStarted: () => {
        setVoiceChatData(this, {
          followUpTTSStateText: "小余正在说",
          persistenceStateText: "小余正在回应",
        });
      },
      onTextDelta: (event) => {
        this.applyStreamingAssistantText(event.text, sourceMessageId);
      },
      onAudioFrame: () => {
        this.prepareStreamingAudioPlayback(requestSerial);
      },
      onAudioFrameScheduled: (event) => {
        this.markStreamingAudioFrameScheduled(event);
        this.startStreamingProgressTimer(this.ttsPlayToken);
        this.updateStreamingTTSProgress();
      },
      onAudioDrained: (event) => {
        this.stopAssistantBargeInMonitor();
        const durationSeconds = Number(event.duration_seconds);
        this.ttsProgress.setStreamingDurationSeconds(durationSeconds);
        this.stopFollowUpProgressTimer();
        setVoiceChatData(this, {
          followUpTTSStateText: "正在整理回应",
        });
      },
      onCompleted: (event) => {
        this.applyCompletedStreamingAssistantTurn(event, sourceMessageId);
      },
      onPlaybackError: (error) => {
        this.markStreamingPlaybackError(error, requestSerial);
      },
    }, {
      isActive: () => requestSerial === followUpRequestSerial,
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
    setVoiceChatData(this, {
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

  async markStreamingAutomationEventsVoicePlayed(completed: VoiceAssistantTurnCompletedEvent) {
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

  clearFailedStreamingAssistantTurn(message: string) {
    this.assistantTurnRuntime?.stopPlayback();
    this.assistantTurnRuntime = null;
    this.stopFollowUpAudio();
    const transcriptLines = this.assistantStreamLineId
      ? this.data.transcriptLines.filter((line) => line.id !== this.assistantStreamLineId)
      : this.data.transcriptLines;
    this.assistantStreamLineId = "";
    this.ttsProgress.resetStreamingAudio();
    setVoiceChatData(this, {
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

  applyStreamingAssistantText(text: string, sourceMessageId: string) {
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
    setVoiceChatData(this, {
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

  applyCompletedStreamingAssistantTurn(event: VoiceAssistantTurnCompletedEvent, sourceMessageId: string) {
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
    setVoiceChatData(this, {
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

  prepareStreamingAudioPlayback(requestSerial: number) {
    if (requestSerial !== followUpRequestSerial) {
      return;
    }
    let playToken = this.ttsPlayToken;
    if (!this.audioPlayback.isCurrentToken(playToken)) {
      playToken = this.audioPlayback.nextToken();
      this.ttsPlayToken = playToken;
    }
    const fullText = this.data.assistantPromptText;
    const firstStreamingFrame = !this.ttsProgress.hasStreamingAudioFrame();
    setVoiceChatData(this, {
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

  markStreamingPlaybackError(error: Error, requestSerial: number) {
    if (requestSerial !== followUpRequestSerial) {
      return;
    }
    this.stopAssistantBargeInMonitor();
    this.stopFollowUpProgressTimer();
    setVoiceChatData(this, {
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
    setVoiceChatData(this, {
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
      selectedOpeningTopicText: "",
      persistenceStateText: "已跳过回应",
    });
  },

  chooseOpeningTopic(event: OpeningTopicTapEvent) {
    if (this.data.interviewType === "human_interview") {
      return;
    }
    const promptText = datasetString(event, "promptText");
    if (!promptText || this.data.followUpLoading) {
      return;
    }
    if (this.data.followUpTTSLoading || this.data.followUpTTSPlaying) {
      this.cancelFollowUpTTS("opening_topic_selected");
    }
    setVoiceChatData(this, {
      selectedOpeningTopicText: promptText,
      assistantPromptText: promptText,
      followUpError: "",
      followUpTTSLoading: false,
      followUpTTSError: "",
      followUpTTSStateText: "",
      assistantExpectsAnswer: true,
      canRequestFollowUp: false,
      persistenceStateText: "等待回答",
      autoListenStateText: "可以点按钮开始讲",
    });
  },

  changeSpeakerRole(event: SpeakerRoleTapEvent) {
    if (this.data.recording || this.data.interviewType !== "human_interview") {
      return;
    }
    const role = queryChoice({ role: datasetString(event, "role") }, "role", SPEAKER_ROLE_VALUES, "narrator");
    setVoiceChatData(this, {
      speakerRole: role,
      liveText: "",
      liveCharCount: 0,
      primaryActionText: "开始说话",
    });
  },

  startRecordingAsSpeaker(event: SpeakerRoleTapEvent) {
    if (
      this.data.recording
      || this.recordingStartPromise
      || this.data.finishingSession
      || !this.data.voiceAuthorizationAgreed
      || this.data.interviewType !== "human_interview"
    ) {
      return;
    }
    const role = queryChoice({ role: datasetString(event, "role") }, "role", SPEAKER_ROLE_VALUES, "narrator");
    setVoiceChatData(this, {
      speakerRole: role,
      liveText: "",
      liveCharCount: 0,
      primaryActionText: "开始说话",
    });
    this.startRecording(false, role);
  },

  stopAndAskFollowUp() {
    if (this.data.interviewType === "human_interview") {
      return;
    }
    if (this.data.followUpLoading) {
      return;
    }
    if (this.data.recording && this.voiceStreaming.hasClient()) {
      this.voiceStreaming.stopRecording();
      setVoiceChatData(this, {
        recording: false,
        primaryActionText: "继续说话",
        persistenceStateText: "已暂停收音",
        autoListenStateText: "小余准备问一句",
      });
    }
    void this.generateFollowUp(true);
  },

  enqueueAssistantTurn(item: AssistantTurnQueueItem) {
    if (!item.messageId) {
      return;
    }
    const transcriptLines = upsertTranscriptLine(this.data.transcriptLines, item.line);
    setVoiceChatData(this, {
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
          setVoiceChatData(this, {
            followUpTTSError: error instanceof Error ? error.message : "小余语音生成失败",
            followUpTTSStateText: "语音暂不可用",
          });
        }
      }
    } finally {
      this.assistantTurnQueueRunning = false;
    }
  },

  async prepareAssistantTurnAudio(item: AssistantTurnQueueItem) {
    if (!this.data.voiceSessionId || !item.messageId) {
      throw new Error("缺少要合成的小余回应");
    }
    const requestSerial = this.ttsRequestSerial;
    this.assistantAudioPrepareCount += 1;
    setVoiceChatData(this, {
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
      setVoiceChatData(this, {
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
        setVoiceChatData(this, { followUpTTSLoading: false });
      }
    }
  },

  showAssistantTurn(item: AssistantTurnQueueItem, patch: Partial<VoiceChatData> = {}) {
    const transcriptLines = upsertTranscriptLine(this.data.transcriptLines, item.line);
    setVoiceChatData(this, {
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

  async playPreparedAssistantTurn(item: AssistantTurnQueueItem, prepared: PreparedAssistantTurnAudio) {
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

  async prepareFollowUpTTS(messageId: string, forceRegenerate: boolean = false, autoListenAfterPlayback: boolean = true) {
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
        durationSeconds: this.ttsProgress.streamingDurationSeconds(),
      });
      return;
    }
    if (this.data.followUpMessageId) {
      await this.prepareFollowUpTTS(this.data.followUpMessageId, false, false);
    }
  },

  async playAudioFile(audioFileId: string, options: {
      autoListenAfterPlayback?: boolean;
      text?: string;
      alignment?: TTSAlignmentSegment[];
      durationSeconds?: number;
    } = {}) {
    if (!audioFileId) {
      return;
    }
    this.stopFollowUpAudio();
    const playToken = this.audioPlayback.nextToken();
    this.ttsPlayToken = playToken;
    setVoiceChatData(this, {
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
            this.ttsProgress.markPlaybackStarted();
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
            setVoiceChatData(this, {
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
            setVoiceChatData(this, {
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
        setVoiceChatData(this, {
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
      setVoiceChatData(this, { followUpTTSPlaying: false });
    }
    this.resetFollowUpTTSProgress();
  },

  cancelFollowUpTTS(reason: string = "cancelled") {
    this.ttsRequestSerial += 1;
    const partialAssistantLineId = this.assistantStreamLineId;
    const transcriptLines = partialAssistantLineId
      ? this.data.transcriptLines.filter((line) => line.id !== partialAssistantLineId)
      : this.data.transcriptLines;
    this.assistantTurnRuntime?.cancel(reason);
    this.assistantTurnRuntime = null;
    this.assistantStreamLineId = "";
    this.ttsProgress.resetStreamingAudio();
    this.assistantTurnQueue = [];
    this.assistantAudioPrepareCount = 0;
    this.stopFollowUpAudio();
    if (partialAssistantLineId) {
      setVoiceChatData(this, {
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
    if (this.data.followUpLoading || this.data.followUpTTSLoading || this.data.followUpTTSPlaying) {
      setVoiceChatData(this, {
        followUpLoading: false,
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

  startAssistantBargeInMonitor(requestSerial: number) {
    if (
      this.data.recording
      || !this.data.autoBargeInEnabled
      || !this.assistantTurnActiveForBargeIn()
      || this.assistantBargeInMonitor.isRunning()
    ) {
      if (!this.data.autoBargeInEnabled && this.assistantTurnActiveForBargeIn()) {
        setVoiceChatData(this, { autoListenStateText: "可以点按钮回答" });
      }
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
          setVoiceChatData(this, { autoListenStateText: "可以点按钮回答" });
        }
      },
    });
    if (started) {
      setVoiceChatData(this, { autoListenStateText: "想说时可以直接开口" });
    }
  },

  stopAssistantBargeInMonitor() {
    return this.assistantBargeInMonitor.stopAndWait();
  },

  async handleAssistantBargeIn(requestSerial: number) {
    if (
      requestSerial !== followUpRequestSerial
      || this.data.recording
      || !this.assistantTurnActiveForBargeIn()
    ) {
      await this.stopAssistantBargeInMonitor();
      return;
    }
    setVoiceChatData(this, {
      autoListenStateText: "听到您说话了",
    });
    if (!this.ensureVoiceAuthorization(false)) {
      await this.stopAssistantBargeInMonitor();
      return;
    }
    await this.startRecordingAfterPrivacy(false);
  },

  markStreamingAudioFrameScheduled(event: StreamingSpeechPlaybackFrameEvent) {
    this.ttsProgress.markStreamingAudioFrameScheduled(event);
  },

  updateStreamingTTSProgress() {
    this.ttsProgress.updateStreamingProgress(
      this.data.assistantPromptText,
      this.assistantTurnRuntime?.stats()?.currentTimeSeconds,
      (patch) => this.applyTTSProgressPatch(patch),
    );
  },

  updateFollowUpTTSProgress(currentTime: number, duration: number, fallbackDuration: number = 0) {
    this.ttsProgress.updateAudioFileProgress(
      this.data.assistantPromptText,
      currentTime,
      duration,
      fallbackDuration,
      this.data.ttsAlignment,
      (patch) => this.applyTTSProgressPatch(patch),
    );
  },

  playbackTimeForProgress(audio: WechatMiniprogram.InnerAudioContext) {
    return this.ttsProgress.playbackTimeForAudio(audio);
  },

  streamingProgressPercent(spokenText: string, fullText: string, allowComplete: boolean) {
    return streamingProgressPercent(spokenText, fullText, allowComplete);
  },

  startStreamingProgressTimer(playToken: number) {
    if (playToken !== this.ttsPlayToken) {
      return;
    }
    this.ttsProgress.startStreamingTimer({
      isActive: () => (
        playToken === this.ttsPlayToken
        && Boolean(this.assistantTurnRuntime)
        && this.data.followUpTTSPlaying
      ),
      currentTimeSeconds: () => this.assistantTurnRuntime?.stats()?.currentTimeSeconds,
      text: () => this.data.assistantPromptText,
      applyPatch: (patch) => this.applyTTSProgressPatch(patch),
    });
  },

  startFollowUpProgressTimer(playToken: number, audio: WechatMiniprogram.InnerAudioContext, fallbackDuration: number = 0) {
    this.ttsProgress.startAudioFileTimer({
      audio,
      isActive: () => (
        playToken === this.ttsPlayToken
        && this.audioPlayback.currentAudio() === audio
        && this.data.followUpTTSPlaying
      ),
      text: () => this.data.assistantPromptText,
      alignment: () => this.data.ttsAlignment,
      fallbackDurationSeconds: fallbackDuration,
      applyPatch: (patch) => this.applyTTSProgressPatch(patch),
    });
  },

  stopFollowUpProgressTimer() {
    this.ttsProgress.stopTimer();
  },

  resetFollowUpTTSProgress() {
    this.ttsProgress.resetProgress((patch) => this.applyTTSProgressPatch(patch));
  },

  applyTTSProgressPatch(patch: VoiceAssistantTTSProgressPatch) {
    setVoiceChatData(this, patch);
  },

  toggleAutoStop() {
    const autoStopEnabled = !this.data.autoStopEnabled;
    this.voiceStreaming.configureAutoStop(autoStopEnabled ? AUTO_STOP_SILENCE_MS : 0);
    setVoiceChatData(this, {
      autoStopEnabled,
      autoStopStateText: autoStopEnabled ? "自动收尾开" : "自动收尾关",
    });
  },

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
    setVoiceChatData(this, { connecting: true, error: "" });
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
      setVoiceChatData(this, { connecting: false });
      if (connected) {
        await this.tryStartAutoListenAfterTTS();
      }
    }
    return connected;
  },

  startRecording(autoTriggered: boolean = false, speakerRole?: MemoirSpeakerRole) {
    if (this.data.finishingSession || !this.ensureVoiceAuthorization(autoTriggered)) {
      return false;
    }
    if (this.recordingStartPromise) {
      return true;
    }
    setVoiceChatData(this, voiceRecordingStartRequestedPatch());
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
        setVoiceChatData(this, { recordingStarting: false });
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
    if (!autoTriggered) {
      this.consecutiveNoAnswerPrompts = 0;
      this.cancelPendingNoAnswerPrompt(!interruptingAssistant);
    }
    if (interruptingAssistant) {
      followUpRequestSerial += 1;
      this.cancelFollowUpTTS("interrupted_by_user");
      setVoiceChatData(this, voiceAssistantInterruptedPatch());
    }
    await this.stopAssistantBargeInMonitor();
    if (
      requestGeneration !== this.recordingStartGeneration
      || this.pageSuspended
      || this.data.finishingSession
    ) {
      return false;
    }
    if (!(await this.syncVoiceAuthorizationForSession())) {
      return false;
    }
    try {
      await ensureRecorderPrivacyAuthorization();
    } catch (error) {
      const message = error instanceof Error ? error.message : "微信隐私授权失败";
      this.handleError(message, { kind: "recorder", code: "wechat_privacy_authorization_failed", message });
      setVoiceChatData(this, voiceRecorderPrivacyDeniedPatch(autoTriggered, this.data.autoListenStateText));
      return false;
    }
    if (
      requestGeneration !== this.recordingStartGeneration
      || this.pageSuspended
      || this.data.finishingSession
    ) {
      return false;
    }
    if (
      this.data.connecting
      || voiceClientUnavailableForRecording(this.voiceStreaming.hasClient(), this.data.status)
    ) {
      setVoiceChatData(this, voiceRecordingReconnectPatch(autoTriggered, this.data.autoListenStateText));
      const connected = await this.connectGateway();
      if (
        !connected
        || requestGeneration !== this.recordingStartGeneration
        || this.pageSuspended
        || this.data.finishingSession
        || voiceClientUnavailableForRecording(this.voiceStreaming.hasClient(), this.data.status)
      ) {
        setVoiceChatData(this, voiceRecordingUnavailablePatch(autoTriggered, this.data.autoListenStateText));
        return false;
      }
    }
    if (
      requestGeneration !== this.recordingStartGeneration
      || this.pageSuspended
      || this.data.finishingSession
    ) {
      return false;
    }
    const recordingOptions: {
      noVoiceActivityTimeoutMs: number;
      speakerRole?: MemoirSpeakerRole;
    } = {
      noVoiceActivityTimeoutMs: autoTriggered ? AUTO_LISTEN_NO_ANSWER_MS : 0,
    };
    if (this.data.interviewType === "human_interview") {
      recordingOptions.speakerRole = speakerRole || this.data.speakerRole;
    }
    const started = this.voiceStreaming.startRecording(recordingOptions);
    if (!started) {
      setVoiceChatData(this, voiceRecordingUnavailablePatch(autoTriggered, this.data.autoListenStateText));
      return false;
    }
    if (!interruptingAssistant) {
      this.cancelFollowUpTTS();
    }
    this.handledNoAnswerFrameCount = -1;
    setVoiceChatData(this, voiceRecordingStartedPatch(autoTriggered));
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
      setVoiceChatData(this, voiceRecordingStoppedPatch());
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
      setVoiceChatData(this, { followUpLoading: false });
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
    setVoiceChatData(this, noAnswerPromptStartPatch());
    try {
      const response = await createMemoirVoiceNoAnswerPrompt(this.data.voiceSessionId, { attempt });
      if (requestSerial !== this.noAnswerPromptRequestSerial) {
        return;
      }
      this.enqueueAssistantTurn(assistantTurnQueueItem(response, { autoListenAfterPlayback }));
      setVoiceChatData(this, noAnswerPromptQueuedPatch(autoListenAfterPlayback));
    } catch (error) {
      if (requestSerial !== this.noAnswerPromptRequestSerial) {
        return;
      }
      setVoiceChatData(this, noAnswerPromptFailedPatch(
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
        setVoiceChatData(this, { followUpLoading: false });
      }
    }
  },

  async finishSession() {
    if (this.data.finishingSession) {
      return;
    }
    this.recordingStartGeneration += 1;
    this.stopCompletionTaskRefresh();
    this.cancelFollowUpTTS();
    setVoiceChatData(this, finishSessionStartPatch(this.data.recording));
    if (!this.voiceStreaming.hasClient()) {
      await this.refreshVoiceSessionSnapshot();
      setVoiceChatData(this, { finishingSession: false });
      return;
    }
    try {
      await this.voiceStreaming.finishSession();
      trackAuditAction("voice_chat.finish", {}, { voiceSessionId: this.data.voiceSessionId });
      setVoiceChatData(this, finishSessionSavedPatch());
      await this.refreshVoiceSessionSnapshot();
      setVoiceChatData(this, { persistenceStateText: "已保存" });
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
      setVoiceChatData(this, { finishingSession: false });
    }
  },

  handleStatus(status: VoiceChatConnectionStatus) {
    setVoiceChatData(this, voiceConnectionStatusPatch(status, this.data));
  },

  handleCaptureStats(stats: MemoirCaptureStats) {
    const noAnswerAutoStopped = stats.autoStopped && stats.autoStopReason === "no_voice";
    const noAnswerPromptAlreadyHandled = noAnswerAutoStopped
      && this.handledNoAnswerFrameCount === stats.frameCount;
    const { patch, shouldHandleNoAnswerAutoStop } = voiceCaptureStatsPatch(stats, {
      autoStopEnabled: this.data.autoStopEnabled,
      noAnswerPromptAlreadyHandled,
    });
    setVoiceChatData(this, patch);
    if (shouldHandleNoAnswerAutoStop && !this.pageSuspended) {
      this.handledNoAnswerFrameCount = stats.frameCount;
      void this.handleNoAnswerAutoStop();
    }
  },

  handleGatewayEvent(event: VoiceGatewayServerEvent) {
    const nextData = voiceGatewayEventPatch(event, this.data);
    if (Object.keys(nextData).length) {
      setVoiceChatData(this, nextData);
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
    setVoiceChatData(this, voiceLiveTranscriptPatch(text, this.data.liveScrollTop));
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
    setVoiceChatData(this, result.patch);
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
      this.startCompletionTaskVerification();
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
    setVoiceChatData(this, voiceChatErrorPatch(message, error, this.data));
  },
});

function voiceAuditText(value: string): string {
  return (value || "").slice(0, 240);
}
