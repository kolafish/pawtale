import type {
  MemoirGuidanceMode,
  MemoirInterviewType,
  MemoirSessionGoal,
  MemoirSpeakerRole,
  MemoirVoiceSessionDetail,
  VoiceChatTranscriptLine,
  VoiceGatewayServerEvent,
} from "../../domain/memoir-voice-session";
import { definePage } from "../../services/page-definition";
import { trackAuditAction } from "../../services/audit-tracker";
import { AudioPlaybackController } from "../../services/audio-playback";
import { readAccessibilitySettings } from "../../services/accessibility-settings";
import { redirectToTranscripts } from "../../services/navigation";
import {
  type AssistantTurnQueueItem,
} from "../../services/voice-assistant-turn-queue";
import {
  type VoiceAssistantTurnCompletedEvent,
} from "../../services/voice-assistant-turn-stream";
import {
  VoiceAssistantTurnRuntime,
} from "../../services/voice-assistant-turn-runtime";
import { StreamingVoiceController } from "../../services/streaming-voice-controller";
import { VoiceBargeInMonitor } from "../../services/voice-barge-in-monitor";
import {
  getMemoirVoiceSession,
  markMemoirAutomationEventsVoicePlayed,
  stopMemoirVoiceSession,
} from "../../services/memoir-voice-session-api";
import { canAutoStartListeningAfterTTS } from "../../services/voice-chat-auto-listen";
import {
  VoiceAssistantTTSProgressRuntime,
} from "../../services/voice-assistant-tts-progress";
import {
  ensureVoiceRecordingAuthorization,
  readVoiceAuthorizationConsent,
  saveVoiceAuthorizationConsent,
  syncVoiceAuthorizationConsent,
  VOICE_AUTHORIZATION_PAGE_URL,
} from "../../services/voice-authorization";
import {
  assistantTranscriptState,
  canRequestFollowUp,
  followUpSourceMessageId,
  latestAssistantMessageId,
  statusText,
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
  applyVoiceChatDataPatch,
  bookCompletionRoundFinished,
  initialVoiceChatData,
  voiceAuthorizationChangePatch,
  voiceAuthorizationConsentPatch,
  voiceAuthorizationRequiredPatch,
  voiceAuthorizationSyncFailedPatch,
  voiceAuthorizationWaitingPatch,
  voiceChatAutoListenGateSnapshot,
  voiceChatSessionEntryPatch,
  voiceSessionDetailPatch,
  voiceSessionSnapshotPatch,
} from "./voice-chat-page-state";
import {
  finishSessionSavedPatch,
  finishSessionStartPatch,
} from "./voice-chat-recording-flow";
import { VoiceChatCompletionIssueRuntime } from "./voice-chat-completion-issue-runtime";
import { voiceChatAssistantPlaybackMethods } from "./voice-chat-assistant-playback";
import {
  AUTO_STOP_SILENCE_MS,
  voiceChatRecordingRuntimeMethods,
} from "./voice-chat-recording-runtime";
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
const GUIDANCE_MODE_VALUES: readonly MemoirGuidanceMode[] = ["free_narration", "guided_interview"];
const INTERVIEW_TYPE_VALUES: readonly MemoirInterviewType[] = ["ai_interview", "human_interview"];
const SESSION_GOAL_VALUES: readonly MemoirSessionGoal[] = ["open_narration", "book_completion"];
const SPEAKER_ROLE_VALUES: readonly MemoirSpeakerRole[] = ["narrator", "interviewer"];

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
  followUpRequestSerial: 0,
  autoListenPendingAfterPlayback: false,
  pageSuspended: false,
  gatewayConnectPromise: null as Promise<boolean> | null,
  recordingStartPromise: null as Promise<boolean> | null,
  recordingStartGeneration: 0,
  completionIssueRuntime: null as VoiceChatCompletionIssueRuntime | null,

  data: initialVoiceChatData(),

  completionIssues() {
    if (!this.completionIssueRuntime) {
      this.completionIssueRuntime = new VoiceChatCompletionIssueRuntime({
        readState: () => this.data,
        applyPatch: (patch) => applyVoiceChatDataPatch(this, patch),
        isSuspended: () => this.pageSuspended,
        cancelAssistant: (reason) => this.cancelFollowUpTTS(reason),
      });
    }
    return this.completionIssueRuntime;
  },

  autoListenGateSnapshot() {
    const hasClient = this.voiceStreaming.hasClient();
    const canStart = hasClient
      && !bookCompletionRoundFinished(this.data)
      && canAutoStartListeningAfterTTS({
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
    applyVoiceChatDataPatch(this, voiceChatSessionEntryPatch({
      voiceSessionId,
      projectTitle,
      guidanceMode,
      currentGuidanceModule,
      interviewType,
      sessionGoal,
      voiceAuthorizationAgreed,
    }));
    if (!voiceSessionId) {
      applyVoiceChatDataPatch(this, { error: "缺少本次语音记录" });
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
      applyVoiceChatDataPatch(this, voiceAuthorizationWaitingPatch());
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
    applyVoiceChatDataPatch(this, {
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
    this.stopCompletionIssueRefresh();
    this.recordingStartGeneration += 1;
    this.followUpRequestSerial += 1;
    this.noAnswerPromptRequestSerial += 1;
    this.noAnswerPromptInFlight = false;
    this.persistCurrentTranscriptSnapshot();
    this.persistPendingVoiceClose(reason);
    this.cancelFollowUpTTS(reason === "hide" ? "page_hidden" : "page_unloaded");
    void this.stopAssistantBargeInMonitor();
    if (this.data.recordingStarting) {
      applyVoiceChatDataPatch(this, { recordingStarting: false });
    }
    if (this.data.recording && this.voiceStreaming.hasClient()) {
      this.voiceStreaming.stopRecording();
      applyVoiceChatDataPatch(this, {
        recording: false,
        primaryActionText: "继续说话",
        persistenceStateText: "正在保存当前句",
        recoveryNotice: "已暂停收音，返回后会恢复已保存的讲述记录",
      });
    }
  },

  refreshVoiceAuthorizationConsent() {
    const voiceAuthorizationAgreed = readVoiceAuthorizationConsent();
    applyVoiceChatDataPatch(this, voiceAuthorizationConsentPatch(this.data, voiceAuthorizationAgreed));
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
    applyVoiceChatDataPatch(this, voiceAuthorizationChangePatch(this.data, consent.agreed));
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
        applyVoiceChatDataPatch(this, { voiceAuthorizationAgreed: true, error: "" });
      }
      return true;
    }
    applyVoiceChatDataPatch(this, voiceAuthorizationRequiredPatch(autoTriggered));
    wx.showToast({ title: "请先同意声纹授权协议", icon: "none" });
    return false;
  },

  async syncVoiceAuthorizationForSession(forRecording: boolean = false) {
    if (!readVoiceAuthorizationConsent()) {
      this.ensureVoiceAuthorization(false);
      return false;
    }
    try {
      if (forRecording) {
        return await ensureVoiceRecordingAuthorization(this.data.voiceSessionId || undefined);
      }
      await syncVoiceAuthorizationConsent({
        sourceSessionId: this.data.voiceSessionId || undefined,
      });
      return true;
    } catch (error) {
      const fallback = forRecording ? "录音授权失败" : "声纹授权同步失败";
      const message = error instanceof Error ? error.message : fallback;
      if (forRecording) {
        this.handleError(message, { kind: "recorder", code: "voice_recording_authorization_failed", message });
      }
      applyVoiceChatDataPatch(this, voiceAuthorizationSyncFailedPatch(message, forRecording));
      if (typeof wx !== "undefined" && typeof wx.showToast === "function") {
        wx.showToast({ title: fallback, icon: "none" });
      }
      return false;
    }
  },

  async syncVoiceAuthorizationAndConnect(autoTriggered: boolean = false) {
    const synced = await this.syncVoiceAuthorizationForSession();
    if (!synced) {
      if (autoTriggered) {
        applyVoiceChatDataPatch(this, { autoListenStateText: "自动收音已暂停，等待声纹授权同步" });
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
    applyVoiceChatDataPatch(this, voiceSessionSnapshotPatch(snapshot, this.data));
  },

  async refreshVoiceSessionSnapshot() {
    if (!this.data.voiceSessionId) {
      return null;
    }
    try {
      const detail = await getMemoirVoiceSession(this.data.voiceSessionId);
      writeMemoirVoiceSessionSnapshot(this.data.voiceSessionId, { detail });
      this.applyVoiceSessionDetail(detail);
      await this.refreshCompletionIssue();
      this.reconcilePendingClose(detail);
      if (detail.session.status !== "stopped" && detail.session.status !== "failed") {
        this.maybeGenerateOpeningFollowUp();
      }
      return detail;
    } catch (error) {
      console.warn("refresh voice session snapshot failed", error);
      const message = error instanceof Error ? error.message : "语音会话加载失败";
      applyVoiceChatDataPatch(this, {
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
      applyVoiceChatDataPatch(this, {
        recoveryNotice: "",
        canReconnect: false,
        canFinishAfterDisconnect: false,
      });
      return;
    }
    applyVoiceChatDataPatch(this, {
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
      applyVoiceChatDataPatch(this, {
        persistenceStateText: persisted ? "已保存" : "已识别，记录同步中",
      });
    } catch (error) {
      console.warn("confirm saved voice transcript failed", error);
      applyVoiceChatDataPatch(this, { persistenceStateText: "已保存，本地已缓存" });
    }
  },

  applyVoiceSessionDetail(detail: MemoirVoiceSessionDetail) {
    applyVoiceChatDataPatch(this, voiceSessionDetailPatch(detail, this.data));
  },

  async refreshCompletionIssue(silent = false) {
    return this.completionIssues().refresh(silent);
  },

  stopCompletionIssueRefresh() {
    this.completionIssueRuntime?.stop();
  },

  startCompletionIssueVerification() {
    this.completionIssues().startVerification();
  },

  chooseCompletionIssueResolution() {
    if (this.data.completionIssueAction) {
      return;
    }
    wx.showActionSheet({
      itemList: ["暂时想不起来", "以后再补", "不想继续讨论"],
      success: (result) => {
        const outcome = ([
          "not_remembered",
          "deferred",
          "declined",
        ] as const)[result.tapIndex];
        if (outcome) {
          void this.closeCompletionIssue(outcome);
        }
      },
    });
  },

  async closeCompletionIssue(
    outcome: "not_remembered" | "deferred" | "declined",
  ) {
    await this.completionIssues().close(outcome);
  },

  async finishCompletionRound() {
    if (
      this.data.completionIssueControlsDisabled
      || this.data.finishingSession
      || this.data.recording
    ) {
      return;
    }
    this.recordingStartGeneration += 1;
    this.stopCompletionIssueRefresh();
    this.cancelFollowUpTTS();
    applyVoiceChatDataPatch(this, finishSessionStartPatch(false));
    try {
      if (this.voiceStreaming.hasClient()) {
        await this.voiceStreaming.finishSession();
      } else {
        await stopMemoirVoiceSession(this.data.voiceSessionId);
      }
      trackAuditAction(
        "voice_chat.finish",
        { destination: "book_completion_overview" },
        { voiceSessionId: this.data.voiceSessionId },
      );
      applyVoiceChatDataPatch(this, finishSessionSavedPatch());
      wx.navigateBack({ delta: 1 });
    } catch (error) {
      await this.refreshVoiceSessionSnapshot();
      this.handleError(error instanceof Error ? error.message : "语音保存失败");
    } finally {
      applyVoiceChatDataPatch(this, { finishingSession: false });
    }
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
    applyVoiceChatDataPatch(this, {
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
    applyVoiceChatDataPatch(this, {
      recording: false,
      status: "closed",
      statusText: statusText("closed"),
      primaryActionText: "已保存",
      canReconnect: false,
      canFinishAfterDisconnect: false,
    });
    redirectToTranscripts();
  },

  maybeGenerateOpeningFollowUp() {
    if (
      this.data.interviewType === "human_interview"
      || this.data.sessionGoal === "book_completion"
      || this.pageSuspended
      || this.data.followUpLoading
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
      || this.data.sessionGoal === "book_completion"
      || this.pageSuspended
      || !this.data.voiceSessionId
      || this.data.followUpLoading
    ) {
      return;
    }
    const sourceMessageId = followUpSourceMessageId(this.data.transcriptLines);
    if (!regenerate && this.data.lastFollowUpSourceMessageId === sourceMessageId) {
      return;
    }
    const requestSerial = this.followUpRequestSerial + 1;
    this.followUpRequestSerial = requestSerial;
    this.cancelFollowUpTTS();
    this.assistantStreamLineId = `assistant-stream-${requestSerial}`;
    this.ttsProgress.resetStreamingAudio();
    applyVoiceChatDataPatch(this, {
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
      if (requestSerial !== this.followUpRequestSerial) {
        return;
      }
      this.clearFailedStreamingAssistantTurn(error instanceof Error ? error.message : "小余回应生成失败");
    } finally {
      if (requestSerial === this.followUpRequestSerial) {
        applyVoiceChatDataPatch(this, {
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
        applyVoiceChatDataPatch(this, {
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
        applyVoiceChatDataPatch(this, {
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
      isActive: () => requestSerial === this.followUpRequestSerial,
    });
    this.assistantTurnRuntime = runtime;
    const { completed, playbackError } = await runtime.start({ regenerate });
    if (requestSerial !== this.followUpRequestSerial) {
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
    applyVoiceChatDataPatch(this, {
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
    applyVoiceChatDataPatch(this, {
      ...assistantTranscriptState(transcriptLines, {
        guidanceMode: this.data.guidanceMode,
        lastFollowUpSourceMessageId: this.data.lastFollowUpSourceMessageId,
        followUpLoading: false,
        assistantPromptText: "",
        followUpMessageId: "",
        followUpAudioFileId: "",
        followUpTTSStateText: "",
        ttsAlignment: [],
        assistantExpectsAnswer: this.data.sessionGoal === "book_completion"
          && this.data.transcriptLines.some((line) => line.role === "user")
          ? false
          : true,
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
    applyVoiceChatDataPatch(this, {
      ...assistantTranscriptState(transcriptLines, {
        guidanceMode: this.data.guidanceMode,
        lastFollowUpSourceMessageId: this.data.lastFollowUpSourceMessageId,
        followUpLoading: true,
        assistantPromptText: text,
        followUpMessageId: "",
        followUpAudioFileId: "",
        followUpTTSStateText: this.data.followUpTTSStateText,
        ttsAlignment: [],
        assistantExpectsAnswer: this.data.sessionGoal === "book_completion"
          && this.data.transcriptLines.some((line) => line.role === "user")
          ? false
          : true,
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
    applyVoiceChatDataPatch(this, {
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
    if (requestSerial !== this.followUpRequestSerial) {
      return;
    }
    let playToken = this.ttsPlayToken;
    if (!this.audioPlayback.isCurrentToken(playToken)) {
      playToken = this.audioPlayback.nextToken();
      this.ttsPlayToken = playToken;
    }
    const fullText = this.data.assistantPromptText;
    const firstStreamingFrame = !this.ttsProgress.hasStreamingAudioFrame();
    applyVoiceChatDataPatch(this, {
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
    if (firstStreamingFrame) {
      void this.startAssistantBargeInMonitor(requestSerial);
    }
  },

  markStreamingPlaybackError(error: Error, requestSerial: number) {
    if (requestSerial !== this.followUpRequestSerial) {
      return;
    }
    this.stopAssistantBargeInMonitor();
    this.stopFollowUpProgressTimer();
    applyVoiceChatDataPatch(this, {
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
    applyVoiceChatDataPatch(this, {
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
    applyVoiceChatDataPatch(this, {
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
    applyVoiceChatDataPatch(this, {
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
    applyVoiceChatDataPatch(this, {
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
      applyVoiceChatDataPatch(this, {
        recording: false,
        primaryActionText: "继续说话",
        persistenceStateText: "已暂停收音",
        autoListenStateText: "小余准备问一句",
      });
    }
    void this.generateFollowUp(true);
  },

  ...voiceChatAssistantPlaybackMethods,

  toggleAutoStop() {
    const autoStopEnabled = !this.data.autoStopEnabled;
    this.voiceStreaming.configureAutoStop(autoStopEnabled ? AUTO_STOP_SILENCE_MS : 0);
    applyVoiceChatDataPatch(this, {
      autoStopEnabled,
      autoStopStateText: autoStopEnabled ? "自动收尾开" : "自动收尾关",
    });
  },

  toggleHistory() {
    if (!this.data.transcriptLines.length) {
      return;
    }
    applyVoiceChatDataPatch(this, { historyExpanded: !this.data.historyExpanded });
  },

  ...voiceChatRecordingRuntimeMethods,

});
