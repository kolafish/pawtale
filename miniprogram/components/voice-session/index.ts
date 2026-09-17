import type {
  MemoirSpeakerRole,
  MemoirVoiceSessionDetail,
  VoiceChatTranscriptLine,
  VoiceGatewayServerEvent,
} from "../../domain/memoir-voice-session";
import { voiceSessionRuntime } from "./voice-session-runtime";
import type { VoiceSessionEntry, VoiceSessionExit } from "./events";
import { trackAuditAction } from "../../services/audit-tracker";
import { readAccessibilitySettings } from "../../services/accessibility-settings";
import {
  type VoiceAssistantTurnCompletedEvent,
} from "../../services/voice-assistant-turn-stream";
import {
  VoiceAssistantTurnRuntime,
} from "../../services/voice-assistant-turn-runtime";
import {
  getMemoirVoiceSession,
  markMemoirAutomationEventsVoicePlayed,
  stopMemoirVoiceSession,
} from "../../services/memoir-voice-session-api";
import { getMemoirDraftProgress } from "../../services/memoir-snapshot-api";
import { storyProgressTitle } from "../../services/story-growth-notice";
import { canAutoStartListeningAfterTTS } from "../../services/voice-chat-auto-listen";
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
  voiceChatRecordingRuntimeMethods,
} from "./voice-chat-recording-runtime";
import {
  datasetString,
  queryChoice,
  type PageDatasetEvent,
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
const SPEAKER_ROLE_VALUES: readonly MemoirSpeakerRole[] = ["narrator", "interviewer"];

Component({
  properties: {
    entry: { type: Object, value: null },
    embedded: { type: Boolean, value: false },
  },
  data: initialVoiceChatData(),
  lifetimes: {
    attached() { void this.onLoad(this.properties.entry as VoiceSessionEntry); },
    detached() { this.onUnload(); },
  },
  pageLifetimes: {
    show() { this.onShow(); },
    hide() { this.onHide(); },
  },
  methods: {
  runtime() { return voiceSessionRuntime(this); },
  async returnToHome() {
    if (!this.properties.embedded || this.data.finishingSession || this.runtime().pageSuspended) return;
    trackAuditAction("voice_chat.return_home", {}, { voiceSessionId: this.data.voiceSessionId });
    await this.finishSession();
  },
  exitSession(detail: VoiceSessionExit) {
    const runtime = this.runtime();
    if (runtime.disposed) return;
    if (runtime.pageSuspended) {
      runtime.pendingExit = detail;
    } else {
      runtime.pendingExit = null;
      this.triggerEvent("exit", { ...detail, voiceSessionId: this.data.voiceSessionId });
    }
  },

  completionIssues() {
    const runtime = this.runtime();
    if (!runtime.completionIssueRuntime) {
      runtime.completionIssueRuntime = new VoiceChatCompletionIssueRuntime({
        readState: () => this.data,
        applyPatch: (patch) => applyVoiceChatDataPatch(this, patch),
        isSuspended: () => this.runtime().pageSuspended,
        cancelAssistant: (reason) => this.cancelFollowUpTTS(reason),
      });
    }
    return runtime.completionIssueRuntime;
  },

  autoListenGateSnapshot() {
    const hasClient = this.runtime().voiceStreaming.hasClient();
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
      pendingAfterPlayback: this.runtime().autoListenPendingAfterPlayback,
      canStart,
    });
  },

  async onLoad(entry: VoiceSessionEntry) {
    if (this.data.voiceSessionId) return;
    this.refreshAccessibilitySettings();
    const { voiceSessionId, mode: guidanceMode = "free_narration", interviewType = "ai_interview",
      sessionGoal = "open_narration", module: currentGuidanceModule = "", projectTitle = "" } = entry;
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
    if (this.runtime().disposed) return;
    const wasSuspended = this.runtime().pageSuspended;
    this.runtime().pageSuspended = false;
    const pendingExit = this.runtime().pendingExit;
    if (pendingExit) {
      this.exitSession(pendingExit);
      return;
    }
    this.startProgressNoticeWatch();
    this.refreshAccessibilitySettings();
    this.refreshVoiceAuthorizationConsent();
    if (wasSuspended && this.data.storyGrowthNoticeState === "pending") {
      this.startStoryGrowthWatch();
    }
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
      if (this.runtime().voiceStreaming.isBargeInMonitoring() && this.runtime().recordingStartPromise) {
        this.runtime().recordingStartGeneration += 1;
      }
      void this.runtime().voiceStreaming.stopBargeInMonitoring(true);
    }
  },

  onHide() {
    this.suspendVoiceChat("hide");
  },

  onUnload() {
    this.runtime().disposed = true;
    this.suspendVoiceChat("unload");
    this.runtime().voiceStreaming.close();
  },

  suspendVoiceChat(reason: "hide" | "unload") {
    this.runtime().pageSuspended = true;
    this.stopCompletionIssueRefresh();
    this.runtime().recordingStartGeneration += 1;
    this.runtime().followUpRequestSerial += 1;
    this.runtime().noAnswerPromptRequestSerial += 1;
    this.runtime().noAnswerPromptInFlight = false;
    this.stopStoryGrowthWatch();
    this.runtime().progressNoticeWatcher.stop();
    this.persistCurrentTranscriptSnapshot();
    this.persistPendingVoiceClose(reason);
    this.cancelFollowUpTTS(reason === "hide" ? "page_hidden" : "page_unloaded");
    void this.runtime().voiceStreaming.stopBargeInMonitoring(true);
    if (this.data.recordingStarting) {
      applyVoiceChatDataPatch(this, { recordingStarting: false });
    }
    if (this.data.recording && this.runtime().voiceStreaming.hasClient()) {
      this.runtime().voiceStreaming.stopRecording();
      applyVoiceChatDataPatch(this, {
        recording: false,
        primaryActionText: "继续说话",
        persistenceStateText: "正在保存当前句",
        recoveryNotice: "已暂停收音，返回后会恢复已保存的讲述记录",
      });
    }
  },

  stopStoryGrowthWatch() {
    this.runtime().storyGrowthWatchToken += 1;
    this.runtime().storyGrowthTimer.cancel();
  },

  startStoryGrowthWatch() {
    this.stopStoryGrowthWatch();
    const token = ++this.runtime().storyGrowthWatchToken;
    void this.watchStoryGrowth(token);
  },

  async watchStoryGrowth(token: number) {
    const projectId = this.data.projectId;
    const sessionId = this.data.voiceSessionId;
    const current = () => token === this.runtime().storyGrowthWatchToken && !this.runtime().pageSuspended
      && projectId === this.data.projectId && sessionId === this.data.voiceSessionId;
    if (!current()) return;
    try {
      const progress = await getMemoirDraftProgress(projectId, sessionId);
      if (!current()) return;
      const pending = progress.status === "processing" || progress.status === "idle";
      applyVoiceChatDataPatch(this, {
        storyGrowthNoticeState: pending ? "pending" : progress.status === "failed" ? "failed" : progress.status === "needs_input" ? "needs_input" : progress.status === "no_change" ? "no_change" : "ready",
        storyGrowthNoticeTitle: progress.title,
        storyGrowthNoticeDetail: progress.detail,
        storyGrowthNoticeActionText: pending ? "看整理进度" : "查看故事",
      });
      if (!pending) return;
    } catch (error) {
      if (!current()) return;
      applyVoiceChatDataPatch(this, { storyGrowthNoticeDetail: "进度暂未刷新，后台会继续处理" });
      console.warn("watch story growth failed", error);
    }
    if (current()) {
      this.runtime().storyGrowthTimer.schedule(() => { void this.watchStoryGrowth(token); });
    }
  },

  handleStoryGrowthNoticeAction() {
    this.exitSession({ destination: "story", projectId: this.data.projectId, narrationSaved: false });
  },

  startProgressNoticeWatch() {
    if (this.properties.embedded) return;
    const projectId = this.data.projectId;
    if (this.runtime().pageSuspended || !projectId || this.runtime().progressNoticeWatcher.projectId === projectId) return;
    this.runtime().progressNoticeWatcher.start(projectId, (events) => {
      if (this.runtime().pageSuspended || this.data.projectId !== projectId) return;
      const event = events[0];
      applyVoiceChatDataPatch(this, {
        progressNoticeTitle: event ? storyProgressTitle(event) : "",
        progressNoticeDetail: event?.summary_text || "",
      });
    });
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
      && !this.runtime().voiceStreaming.hasClient()
    ) {
      void this.syncVoiceAuthorizationAndConnect();
    }
  },

  async changeVoiceAuthorization(event: CheckboxChangeEvent) {
    const agreed = event.detail.value.includes(VOICE_AUTHORIZATION_VALUE);
    const consent = saveVoiceAuthorizationConsent(agreed);
    if (!agreed) {
      this.runtime().recordingStartGeneration += 1;
      void this.runtime().voiceStreaming.stopBargeInMonitoring(true);
      if (this.data.recording) this.runtime().voiceStreaming.stopRecording();
    }
    applyVoiceChatDataPatch(this, voiceAuthorizationChangePatch(this.data, consent.agreed));
    if (agreed && this.data.voiceSessionId && !this.runtime().voiceStreaming.hasClient() && !this.data.connecting) {
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
    if (this.runtime().pageSuspended || this.runtime().disposed) return false;
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
      if (this.runtime().disposed) return null;
      const expectedProject = (this.properties.entry as VoiceSessionEntry | null)?.projectId;
      if (expectedProject && expectedProject !== detail.session.project_id) {
        throw new Error("这次讲述不属于当前回忆录，请重新进入。");
      }
      writeMemoirVoiceSessionSnapshot(this.data.voiceSessionId, { detail });
      this.applyVoiceSessionDetail(detail);
      await this.refreshCompletionIssue();
      this.reconcilePendingClose(detail);
      if (detail.session.status !== "stopped" && detail.session.status !== "failed") {
        this.maybeGenerateOpeningFollowUp();
      }
      return detail;
    } catch (error) {
      if (this.runtime().disposed) return null;
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
      if (this.runtime().disposed) return;
      writeMemoirVoiceSessionSnapshot(this.data.voiceSessionId, { detail });
      this.applyVoiceSessionDetail(detail);
      const persisted = detail.messages.some((message) => message.id === event.message_id)
        && detail.utterances.some((utterance) => utterance.id === event.utterance_id);
      applyVoiceChatDataPatch(this, {
        persistenceStateText: persisted ? "已保存" : "已识别，记录同步中",
      });
    } catch (error) {
      if (this.runtime().disposed) return;
      console.warn("confirm saved voice transcript failed", error);
      applyVoiceChatDataPatch(this, { persistenceStateText: "已保存，本地已缓存" });
    }
  },

  applyVoiceSessionDetail(detail: MemoirVoiceSessionDetail) {
    if (detail.session.project_id !== this.data.projectId) {
      applyVoiceChatDataPatch(this, { progressNoticeTitle: "", progressNoticeDetail: "" });
    }
    applyVoiceChatDataPatch(this, voiceSessionDetailPatch(detail, this.data));
    this.startProgressNoticeWatch();
  },

  async refreshCompletionIssue(silent = false) {
    return this.completionIssues().refresh(silent);
  },

  stopCompletionIssueRefresh() {
    this.runtime().completionIssueRuntime?.stop();
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
    this.runtime().recordingStartGeneration += 1;
    this.stopCompletionIssueRefresh();
    this.cancelFollowUpTTS();
    applyVoiceChatDataPatch(this, finishSessionStartPatch(false));
    try {
      if (this.runtime().voiceStreaming.hasClient()) {
        await this.runtime().voiceStreaming.finishSession();
      } else {
        await stopMemoirVoiceSession(this.data.voiceSessionId);
      }
      const saved = await this.refreshVoiceSessionSnapshot();
      if (saved?.session.status !== "stopped") {
        this.handleError("暂未确认保存结果，请留在此页稍后重试。");
        return;
      }
      trackAuditAction(
        "voice_chat.finish",
        { destination: "book_completion_overview" },
        { voiceSessionId: this.data.voiceSessionId },
      );
      applyVoiceChatDataPatch(this, finishSessionSavedPatch());
      this.exitSession({ destination: "completion" });
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
    this.runtime().voiceStreaming.close({ force: true });
    await this.refreshVoiceSessionSnapshot();
    applyVoiceChatDataPatch(this, {
      recording: false,
      status: "closed",
      statusText: statusText("closed"),
      primaryActionText: "已保存",
      canReconnect: false,
      canFinishAfterDisconnect: false,
    });
    this.exitSession({ destination: "transcripts" });
  },

  maybeGenerateOpeningFollowUp() {
    if (
      this.data.interviewType === "human_interview"
      || this.data.sessionGoal === "book_completion"
      || this.runtime().pageSuspended
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
      || this.runtime().pageSuspended
      || !this.data.voiceSessionId
      || this.data.followUpLoading
    ) {
      return;
    }
    const sourceMessageId = followUpSourceMessageId(this.data.transcriptLines);
    if (!regenerate && this.data.lastFollowUpSourceMessageId === sourceMessageId) {
      return;
    }
    const requestSerial = this.runtime().followUpRequestSerial + 1;
    this.runtime().followUpRequestSerial = requestSerial;
    this.cancelFollowUpTTS();
    this.runtime().assistantStreamLineId = `assistant-stream-${requestSerial}`;
    this.runtime().ttsProgress.resetStreamingAudio();
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
      if (requestSerial !== this.runtime().followUpRequestSerial) {
        return;
      }
      this.clearFailedStreamingAssistantTurn(error instanceof Error ? error.message : "小余回应生成失败");
    } finally {
      if (requestSerial === this.runtime().followUpRequestSerial) {
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
        this.startStreamingProgressTimer(this.runtime().ttsPlayToken);
        this.updateStreamingTTSProgress();
      },
      onAudioDrained: (event) => {
        this.stopAssistantBargeInMonitor();
        const durationSeconds = Number(event.duration_seconds);
        this.runtime().ttsProgress.setStreamingDurationSeconds(durationSeconds);
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
      isActive: () => requestSerial === this.runtime().followUpRequestSerial,
    });
    this.runtime().assistantTurnRuntime = runtime;
    const { completed, playbackError } = await runtime.start({ regenerate });
    if (requestSerial !== this.runtime().followUpRequestSerial) {
      return;
    }
    this.runtime().assistantTurnRuntime = null;
    if (!playbackError && completed.expects_answer !== false) {
      this.runtime().autoListenPendingAfterPlayback = true;
    }
    if (!playbackError) {
      void this.markStreamingAutomationEventsVoicePlayed(completed);
    }
    this.stopAssistantBargeInMonitor();
    this.stopFollowUpProgressTimer();
    applyVoiceChatDataPatch(this, {
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
    this.runtime().assistantTurnRuntime?.stopPlayback();
    this.runtime().assistantTurnRuntime = null;
    this.stopFollowUpAudio();
    const transcriptLines = this.runtime().assistantStreamLineId
      ? this.data.transcriptLines.filter((line) => line.id !== this.runtime().assistantStreamLineId)
      : this.data.transcriptLines;
    this.runtime().assistantStreamLineId = "";
    this.runtime().ttsProgress.resetStreamingAudio();
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
      id: this.runtime().assistantStreamLineId || `assistant-stream-${Date.now()}`,
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
      line.id !== this.runtime().assistantStreamLineId
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
    if (requestSerial !== this.runtime().followUpRequestSerial) {
      return;
    }
    let playToken = this.runtime().ttsPlayToken;
    if (!this.runtime().audioPlayback.isCurrentToken(playToken)) {
      playToken = this.runtime().audioPlayback.nextToken();
      this.runtime().ttsPlayToken = playToken;
    }
    const fullText = this.data.assistantPromptText;
    const firstStreamingFrame = !this.runtime().ttsProgress.hasStreamingAudioFrame();
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
    if (requestSerial !== this.runtime().followUpRequestSerial) {
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

  startRecordingAsSpeaker(event: SpeakerRoleTapEvent) {
    if (
      this.data.recording
      || this.runtime().recordingStartPromise
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
    if (this.data.recording && this.runtime().voiceStreaming.hasClient()) {
      this.runtime().voiceStreaming.stopRecording();
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

  toggleHistory() {
    if (!this.data.transcriptLines.length) {
      return;
    }
    applyVoiceChatDataPatch(this, { historyExpanded: !this.data.historyExpanded });
  },

  ...voiceChatRecordingRuntimeMethods,

  },
});
