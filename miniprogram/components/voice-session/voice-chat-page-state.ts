import type { OpeningCompletionOffer } from "../../services/voice-assistant-turn-stream";
import type { TTSAlignmentSegment } from "../../domain/memoir-audio";
import { isRecorderPermissionDeniedMessage } from "../../services/wechat-privacy";
import type { MemoirCompletionIssue } from "../../domain/memoir-completion-issue";
import { NARRATION_MODES, narrationModeForSession, type NarrationMode } from "../../domain/memoir-narration-mode";
import type {
  MemoirGuidanceMode,
  MemoirInterviewType,
  MemoirSessionGoal,
  MemoirSpeakerRole,
  MemoirVoiceSessionDetail,
  VoiceChatErrorInfo,
  VoiceChatTranscriptLine,
  VoiceGatewayServerEvent,
} from "../../domain/memoir-voice-session";
import type {
  MemoirCaptureStats,
  VoiceChatConnectionStatus,
} from "../../services/memoir-streaming-asr";
import {
  assistantTranscriptState,
  coveredFollowUpSourceMessageId,
  errorKindText,
  formatDuration,
  gatewayStateText,
  statusText,
} from "../../services/voice-chat-state";
import {
  transcriptLinesFromVoiceSessionDetail,
  type MemoirVoiceSessionSnapshot,
} from "../../stores/memoir-cache";

export interface VoiceChatTranscriptLineView extends VoiceChatTranscriptLine {
  lineClass: string;
  roleText: string;
}

export interface VoiceChatData {
  voiceSessionId: string;
  projectId: string;
  projectTitle: string;
  guidanceMode: MemoirGuidanceMode;
  currentGuidanceModule: string;
  interviewType: MemoirInterviewType;
  sessionGoal: MemoirSessionGoal;
  currentCompletionIssueId: string;
  completionIssue: MemoirCompletionIssue | null;
  openingCompletionOffer: OpeningCompletionOffer | null;
  showOpeningCompletionOffer: boolean;
  completionIssueLoading: boolean;
  completionIssueAction: string;
  completionIssueError: string;
  completionIssueNotice: string;
  speakerRole: MemoirSpeakerRole;
  status: VoiceChatConnectionStatus;
  statusText: string;
  recording: boolean;
  recordingStarting: boolean;
  error: string;
  liveText: string;
  latestTranscriptText: string;
  transcriptLines: VoiceChatTranscriptLine[];
  assistantPromptText: string;
  assistantLoadingText: string;
  assistantEmptyText: string;
  assistantExpectsAnswer: boolean;
  followUpLoading: boolean;
  followUpError: string;
  lastFollowUpSourceMessageId: string;
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
  primaryActionText: string;
  captureDurationText: string;
  audioLevelPercent: number;
  autoStopEnabled: boolean;
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
  recorderSettingsRequired: boolean;
  finishButtonText: string;
  storyGrowthNoticeState: "hidden" | "pending" | "ready" | "failed" | "needs_input" | "no_change";
  storyGrowthNoticeTitle: string;
  storyGrowthNoticeDetail: string;
  storyGrowthNoticeActionText: string;
  progressNoticeTitle: string;
  progressNoticeDetail: string;
  narrationMode: NarrationMode;
  topModeLabel: string;
  topSubtitle: string;
  topicTitleText: string;
  showHumanRecordActions: boolean;
  showControlBar: boolean;
  narratorRecordButtonClass: string;
  interviewerRecordButtonClass: string;
  showErrorKindStrip: boolean;
  showRecoveryPanel: boolean;
  showRecoveryActions: boolean;
  showVoiceAuthorizationPanel: boolean;
  showAssistantPanel: boolean;
  showCompletionIssuePanel: boolean;
  completionIssuePanelClass: string;
  completionIssueStatusText: string;
  completionIssueQuestionText: string;
  showCompletionIssueActions: boolean;
  showCompletionIssueContinue: boolean;
  completionIssueControlsDisabled: boolean;
  completionIssueActionText: string;
  assistantPanelClass: string;
  assistantLabelText: string;
  showAssistantOpening: boolean;
  assistantQuestionText: string;
  showAssistantTTS: boolean;
  assistantDisplayText: string;
  showTTSProgress: boolean;
  showAssistantActions: boolean;
  showTranscriptStage: boolean;
  replayButtonText: string;
  followUpButtonText: string;
  showStageStatus: boolean;
  stageStatusClass: string;
  stageStatusText: string;
  liveDisplayText: string;
  liveCurrentText: string;
  liveTextClass: string;
  liveScrollClass: string;
  liveScrollTop: number;
  liveTranscriptFollowing: boolean;
  liveReviewStartCharCount: number;
  showReturnToLatest: boolean;
  returnToLatestText: string;
  showCaptureStrip: boolean;
  captureStripClass: string;
  showLiveCharCount: boolean;
  showInlineHelp: boolean;
  historyExpanded: boolean;
  historyCountText: string;
  showHistoryPanel: boolean;
  showHistoryLines: boolean;
  historyToggleText: string;
  transcriptLineViews: VoiceChatTranscriptLineView[];
  controlHintText: string;
  micButtonClass: string;
  micButtonDisabled: boolean;
  micButtonProcessing: boolean;
  micButtonText: string;
  largeTextEnabled: boolean;
  autoBargeInEnabled: boolean;
  voiceChatPageClass: string;
}

export interface AutoListenGateSnapshot {
  hasClient: boolean;
  autoListenEnabled: boolean;
  status: VoiceChatConnectionStatus;
  recording: boolean;
  followUpLoading: boolean;
  pendingAfterPlayback: boolean;
  canStart: boolean;
}

export function initialVoiceChatData(): VoiceChatData {
  return {
    voiceSessionId: "",
    projectId: "",
    projectTitle: "",
    guidanceMode: "guided_interview",
    currentGuidanceModule: "",
    interviewType: "ai_interview",
    sessionGoal: "open_narration",
    currentCompletionIssueId: "",
    completionIssue: null,
    openingCompletionOffer: null,
    showOpeningCompletionOffer: false,
    completionIssueLoading: false,
    completionIssueAction: "",
    completionIssueError: "",
    completionIssueNotice: "",
    speakerRole: "narrator",
    status: "idle",
    statusText: "未连接",
    recording: false,
    recordingStarting: false,
    error: "",
    liveText: "",
    latestTranscriptText: "",
    transcriptLines: [],
    assistantPromptText: "",
    assistantLoadingText: "小余正在准备第一句话",
    assistantEmptyText: "小余会先问一句，帮您开始讲。",
    assistantExpectsAnswer: true,
    followUpLoading: false,
    followUpError: "",
    lastFollowUpSourceMessageId: "",
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
    primaryActionText: "开始说话",
    captureDurationText: "00:00",
    audioLevelPercent: 0,
    autoStopEnabled: true,
    autoListenEnabled: true,
    autoListenStateText: "小余说完自动听",
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
    recorderSettingsRequired: false,
    finishButtonText: "保存离开",
    storyGrowthNoticeState: "hidden",
    storyGrowthNoticeTitle: "",
    storyGrowthNoticeDetail: "",
    storyGrowthNoticeActionText: "",
    progressNoticeTitle: "",
    progressNoticeDetail: "",
    narrationMode: "guided_interview",
    topModeLabel: NARRATION_MODES.guided_interview.label,
    topSubtitle: "想讲什么就讲什么，小余在旁边听",
    topicTitleText: "这段回忆",
    showHumanRecordActions: false,
    showControlBar: true,
    narratorRecordButtonClass: "role-record-button narrator",
    interviewerRecordButtonClass: "role-record-button interviewer",
    showErrorKindStrip: false,
    showRecoveryPanel: false,
    showRecoveryActions: false,
    showVoiceAuthorizationPanel: true,
    showAssistantPanel: false,
    showCompletionIssuePanel: false,
    completionIssuePanelClass: "completion-issue-panel",
    completionIssueStatusText: "",
    completionIssueQuestionText: "",
    showCompletionIssueActions: false,
    showCompletionIssueContinue: false,
    completionIssueControlsDisabled: false,
    completionIssueActionText: "",
    assistantPanelClass: "assistant-panel",
    assistantLabelText: "小余想听您讲讲",
    showAssistantOpening: true,
    assistantQuestionText: "今天想先讲哪一段？",
    showAssistantTTS: false,
    assistantDisplayText: "小余会先问一句，帮您开始讲。",
    showTTSProgress: false,
    showAssistantActions: false,
    showTranscriptStage: false,
    replayButtonText: "读给我听",
    followUpButtonText: "让小余问一句",
    showStageStatus: true,
    stageStatusClass: "stage-status",
    stageStatusText: "可以开始讲了",
    liveDisplayText: "点下方“我来说一段”",
    liveCurrentText: "点下方“我来说一段”",
    liveTextClass: "live-text",
    liveScrollClass: "live-scroll",
    liveScrollTop: 0,
    liveTranscriptFollowing: true,
    liveReviewStartCharCount: 0,
    showReturnToLatest: false,
    returnToLatestText: "回到最新",
    showCaptureStrip: true,
    captureStripClass: "capture-strip",
    showLiveCharCount: false,
    showInlineHelp: false,
    historyExpanded: false,
    historyCountText: "0 段 · 0 字",
    showHistoryPanel: false,
    showHistoryLines: false,
    historyToggleText: "查看",
    transcriptLineViews: [],
    controlHintText: "现在可以说了",
    micButtonClass: "mic-button disabled",
    micButtonDisabled: true,
    micButtonProcessing: false,
    micButtonText: "同意后开始说话",
    largeTextEnabled: false,
    autoBargeInEnabled: true,
    voiceChatPageClass: "voice-chat-page",
  };
}

export type VoiceChatPatch = Partial<VoiceChatData>;

export interface VoiceChatDataContext {
  data: VoiceChatData;
  setData(patch: VoiceChatPatch, callback?: () => void): void;
}

export function applyVoiceChatDataPatch(
  context: VoiceChatDataContext,
  patch: VoiceChatPatch,
  callback?: () => void,
): void {
  const nextData: VoiceChatData = { ...context.data, ...patch };
  const values = {
    ...patch,
    ...voiceChatViewPatch(nextData, context.data),
  };
  const changed = Object.fromEntries(
    Object.entries(values).filter(([key, value]) => !Object.is(context.data[key as keyof VoiceChatData], value)),
  ) as VoiceChatPatch;
  if (Object.keys(changed).length) {
    context.setData(changed, callback);
  } else {
    callback?.();
  }
}

export type VoiceChatViewPatch = Pick<
  VoiceChatData,
  | "finishButtonText"
  | "narrationMode"
  | "topModeLabel"
  | "topSubtitle"
  | "topicTitleText"
  | "showHumanRecordActions"
  | "showControlBar"
  | "narratorRecordButtonClass"
  | "interviewerRecordButtonClass"
  | "showErrorKindStrip"
  | "recorderSettingsRequired"
  | "showRecoveryPanel"
  | "showRecoveryActions"
  | "showVoiceAuthorizationPanel"
  | "showAssistantPanel"
  | "showCompletionIssuePanel"
  | "showOpeningCompletionOffer"
  | "completionIssuePanelClass"
  | "completionIssueStatusText"
  | "completionIssueQuestionText"
  | "showCompletionIssueActions"
  | "showCompletionIssueContinue"
  | "completionIssueControlsDisabled"
  | "completionIssueActionText"
  | "assistantPanelClass"
  | "assistantLabelText"
  | "showAssistantOpening"
  | "assistantQuestionText"
  | "showAssistantTTS"
  | "assistantDisplayText"
  | "showTTSProgress"
  | "showAssistantActions"
  | "showTranscriptStage"
  | "replayButtonText"
  | "followUpButtonText"
  | "showStageStatus"
  | "stageStatusClass"
  | "stageStatusText"
  | "liveDisplayText"
  | "liveCurrentText"
  | "liveTextClass"
  | "liveScrollClass"
  | "liveScrollTop"
  | "showReturnToLatest"
  | "returnToLatestText"
  | "showCaptureStrip"
  | "captureStripClass"
  | "showLiveCharCount"
  | "showInlineHelp"
  | "historyCountText"
  | "showHistoryPanel"
  | "showHistoryLines"
  | "historyToggleText"
  | "transcriptLineViews"
  | "controlHintText"
  | "micButtonClass"
  | "micButtonDisabled"
  | "micButtonProcessing"
  | "micButtonText"
  | "voiceChatPageClass"
>;

function largeTextFocusClass(state: VoiceChatData, showAssistantPanel: boolean): string {
  if (!state.largeTextEnabled) {
    return "";
  }
  if (state.recording) {
    return "capture-focus";
  }
  if (showAssistantPanel) {
    return "assistant-focus";
  }
  return "idle-focus";
}

function speakerRoleLabel(role: MemoirSpeakerRole): string {
  return role === "interviewer" ? "采访者" : "被访者";
}

function completionIssueStatusText(state: VoiceChatData): string {
  if (state.completionIssueError) {
    return "补充问题加载失败";
  }
  if (state.completionIssueLoading && !state.completionIssue) {
    return "正在准备要补的问题";
  }
  if (state.completionIssueNotice) {
    return state.completionIssueNotice;
  }
  const status = state.completionIssue?.status;
  if (!status) {
    return "没有拿到本次要补的问题";
  }
  return ({
    open: "等待您补充",
    in_progress: "正在补充这一点",
    closed: "本次补充已经结束",
  })[status];
}

export function bookCompletionRoundFinished(
  state: Pick<
    VoiceChatData,
    "sessionGoal" | "completionIssue" | "assistantExpectsAnswer" | "transcriptLines"
  >,
): boolean {
  if (state.sessionGoal !== "book_completion") {
    return false;
  }
  if (state.completionIssue?.status === "closed") {
    return true;
  }
  return state.assistantExpectsAnswer === false
    && state.transcriptLines.some((line) => line.role === "user");
}

export type VoiceChatInteractionPhase =
  | "idle"
  | "recording"
  | "transcript_processing"
  | "assistant_preparing"
  | "assistant_responding"
  | "waiting_for_answer"
  | "assistant_error";

type VoiceChatInteractionState = Pick<
  VoiceChatData,
  | "recording"
  | "latestTranscriptText"
  | "assistantPromptText"
  | "followUpLoading"
  | "followUpError"
  | "followUpTTSLoading"
  | "followUpTTSPlaying"
>;

function assistantTurnActive(state: VoiceChatInteractionState): boolean {
  return state.followUpLoading || state.followUpTTSLoading || state.followUpTTSPlaying;
}

export function voiceChatInteractionPhase(state: VoiceChatInteractionState): VoiceChatInteractionPhase {
  if (state.recording) {
    return "recording";
  }
  const hasAssistantPrompt = Boolean(state.assistantPromptText);
  if ((hasAssistantPrompt && assistantTurnActive(state)) || state.followUpTTSPlaying) {
    return "assistant_responding";
  }
  if (state.followUpError) {
    return "assistant_error";
  }
  if (hasAssistantPrompt) {
    return "waiting_for_answer";
  }
  if (state.followUpLoading && state.latestTranscriptText) {
    return "transcript_processing";
  }
  if (assistantTurnActive(state)) {
    return "assistant_preparing";
  }
  return "idle";
}

export function voiceChatViewPatch(state: VoiceChatData, previous?: VoiceChatData): VoiceChatViewPatch {
  const interactionPhase = voiceChatInteractionPhase(state);
  const assistantBusy = assistantTurnActive(state);
  const hasAssistantPrompt = Boolean(state.assistantPromptText);
  const isHumanInterview = state.interviewType === "human_interview";
  const isBookCompletion = state.sessionGoal === "book_completion";
  const completionRoundFinished = bookCompletionRoundFinished(state);
  const completionUnavailable = isBookCompletion
    && !state.completionIssueLoading
    && !state.completionIssue;
  const showCompletionIssuePanel = isBookCompletion;
  const showTranscriptProcessing = !isHumanInterview
    && interactionPhase === "transcript_processing";
  const assistantPhaseVisible = interactionPhase === "assistant_preparing"
    || interactionPhase === "assistant_responding"
    || interactionPhase === "waiting_for_answer"
    || interactionPhase === "assistant_error";
  const showAssistantPanel = !isHumanInterview
    && (!completionRoundFinished || assistantBusy)
    && !showTranscriptProcessing
    && (state.recording
      ? hasAssistantPrompt && !isBookCompletion
      : Boolean(assistantPhaseVisible || state.canRequestFollowUp));
  const showHumanRecordActions = isHumanInterview
    && !completionRoundFinished
    && !state.recording
    && !state.recordingStarting
    && state.voiceAuthorizationAgreed;
  const showControlBar = !showHumanRecordActions && !completionRoundFinished;
  const showTranscriptStage = isHumanInterview
    || isBookCompletion
    || state.recording
    || showTranscriptProcessing
    || Boolean(!showAssistantPanel && state.voiceAuthorizationAgreed && (state.liveText || state.latestTranscriptText));
  const savedNarrationLines = state.transcriptLines.filter((line) => line.role !== "assistant");
  const hasSavedNarration = savedNarrationLines.length > 0;
  const savedNarrationCharCount = savedNarrationLines.reduce(
    (total, line) => total + Array.from(line.text.trim()).length,
    0,
  );
  const showStageStatus = !isHumanInterview || Boolean(state.error);
  const showCaptureStrip = (!isHumanInterview || state.recording)
    && !completionRoundFinished;
  let stageStatusText = "可以开始讲了";
  if (state.error) {
    stageStatusText = "需要处理一下";
  } else if (completionUnavailable) {
    stageStatusText = "请重新进入补全成书";
  } else if (completionRoundFinished) {
    stageStatusText = "本次补充已经完成";
  } else if (interactionPhase === "recording") {
    stageStatusText = isHumanInterview ? `正在记录${speakerRoleLabel(state.speakerRole)}` : "我在听，您慢慢说";
  } else if (interactionPhase === "assistant_responding") {
    stageStatusText = state.followUpTTSLoading || state.followUpTTSPlaying
      ? "小余正在读问题"
      : "小余正在准备问题";
  } else if (interactionPhase === "transcript_processing") {
    stageStatusText = "正在帮您整理成文字";
  } else if (interactionPhase === "assistant_preparing") {
    stageStatusText = "小余正在准备问题";
  } else if (state.persistenceStateText === "已保存") {
    stageStatusText = "这一段已经记下来了";
  }
  let liveDisplayText = isHumanInterview ? `点上方开始记录${speakerRoleLabel(state.speakerRole)}` : "现在可以说了";
  if (interactionPhase === "recording") {
    liveDisplayText = state.liveText || (isHumanInterview ? "等待转写..." : "直接说就行，我会帮您记下来");
  } else if (interactionPhase === "transcript_processing") {
    liveDisplayText = state.liveText || state.latestTranscriptText;
  } else if (interactionPhase === "assistant_preparing") {
    liveDisplayText = "小余正在准备第一句话";
  } else if (interactionPhase === "assistant_responding"
    || interactionPhase === "waiting_for_answer"
    || interactionPhase === "assistant_error") {
    liveDisplayText = "现在可以说了";
  } else {
    liveDisplayText = state.liveText
      || state.latestTranscriptText
      || (isHumanInterview ? `点上方开始记录${speakerRoleLabel(state.speakerRole)}` : "现在可以说了");
  }
  if (isBookCompletion && !state.recording && !assistantBusy) {
    liveDisplayText = completionRoundFinished
      ? "本次补充已保存，可以返回了。"
      : (
        state.latestTranscriptText
        || (state.completionIssue
          ? "按您记得的顺序讲一段就好"
          : (state.completionIssueLoading ? "正在准备要补的问题" : "请保存离开后，从首页重新进入"))
      );
  }
  const pendingLiveChars = Math.max(0, state.liveCharCount - state.liveReviewStartCharCount);
  const showReturnToLatest = Boolean(
    state.recording
    && state.liveText
    && !state.liveTranscriptFollowing,
  );
  const liveScrollClass = [
    "live-scroll",
    state.liveScrollClass.split(" ").includes("long") ? "long" : "",
    state.liveTranscriptFollowing ? "" : "reviewing",
  ].filter(Boolean).join(" ");
  let controlHintText = isHumanInterview
    ? ""
    : (isBookCompletion ? "只说您记得的部分就可以" : "现在可以说了");
  if (interactionPhase === "recording") {
    controlHintText = isHumanInterview ? "" : "讲完这段后，可以点这里";
  } else if (!isHumanInterview && interactionPhase === "assistant_preparing") {
    controlHintText = "小余先问一句，稍等一下";
  } else if (!isHumanInterview && interactionPhase === "assistant_responding") {
    controlHintText = "听完就可以开始说";
  }
  if (completionRoundFinished) {
    controlHintText = "";
  } else if (showTranscriptProcessing) {
    controlHintText = "正在帮您整理刚才的故事";
  }
  let micButtonText = state.primaryActionText;
  if (showTranscriptProcessing) {
    micButtonText = "正在帮您整理";
  } else if (!state.voiceAuthorizationAgreed) {
    micButtonText = "同意后开始说话";
  } else if (state.recordingStarting) {
    micButtonText = "正在打开麦克风";
  } else if (state.status === "connecting") {
    micButtonText = "正在准备";
  } else if (completionUnavailable && !state.recording) {
    micButtonText = "请先保存离开";
  } else if (state.recording) {
    micButtonText = isHumanInterview ? "说完了，保存这段" : "讲完这段，请小余接话";
  } else if (!isHumanInterview && (
    interactionPhase === "assistant_preparing"
    || interactionPhase === "assistant_responding"
  )) {
    micButtonText = "我来讲了";
  } else if (state.primaryActionText === "开始说话") {
    micButtonText = isHumanInterview ? `记录${speakerRoleLabel(state.speakerRole)}` : "我来讲了";
  } else if (state.primaryActionText === "继续说话") {
    micButtonText = "继续讲";
  }
  const pageClass = ["voice-chat-page"];
  if (isHumanInterview) {
    pageClass.push("human-interview");
  }
  if (isBookCompletion) {
    pageClass.push("book-completion");
  }
  if (!isHumanInterview && state.recording) {
    pageClass.push("ai-recording-focus");
  }
  if (isHumanInterview && state.recording) {
    pageClass.push("human-recording-focus");
  }
  if (!showControlBar) {
    pageClass.push("no-control-bar");
  }
  const focusClass = largeTextFocusClass(state, showAssistantPanel || showCompletionIssuePanel);
  if (state.largeTextEnabled) {
    pageClass.push("large-text", focusClass);
  }
  const micButtonDisabled = state.recordingStarting
    || showTranscriptProcessing
    || !state.voiceAuthorizationAgreed
    || completionRoundFinished
    || (completionUnavailable && !state.recording);
  const roleRecordButtonSuffix = micButtonDisabled ? " disabled" : "";
  const micButtonClass = [
    "mic-button",
    state.recording ? "recording" : "",
    showTranscriptProcessing ? "processing" : "",
    micButtonDisabled ? "disabled" : "",
  ].filter(Boolean).join(" ");
  const completionIssueStatus = state.completionIssue?.status;
  const showCompletionIssueActions = !completionRoundFinished
    && (completionIssueStatus === "open" || completionIssueStatus === "in_progress");
  const showCompletionIssueContinue = completionRoundFinished
    || completionIssueStatus === "closed";
  const completionIssueControlsDisabled = Boolean(state.completionIssueAction) || state.recording;
  const completionIssuePanelClass = [
    "completion-issue-panel",
    state.completionIssueError ? "error-state" : "",
    completionIssueStatus === "closed" ? "complete-state" : "",
  ].filter(Boolean).join(" ");
  const narrationMode = narrationModeForSession({ guidance_mode: state.guidanceMode,
    interview_type: state.interviewType, session_goal: state.sessionGoal });
  const recorderSettingsRequired = isRecorderPermissionDeniedMessage(state.error);
  return {
    narrationMode,
    voiceChatPageClass: pageClass.filter(Boolean).join(" "),
    finishButtonText: state.finishingSession ? "保存中" : "保存离开",
    topModeLabel: NARRATION_MODES[narrationMode].label,
    topSubtitle: isBookCompletion
      ? "一次只补一个重点"
      : (isHumanInterview
        ? "实时记录采访者和被访者"
        : "想讲什么就讲什么，小余在旁边听"),
    topicTitleText: state.projectTitle || "这段回忆",
    showHumanRecordActions,
    showControlBar,
    narratorRecordButtonClass: `role-record-button narrator${roleRecordButtonSuffix}`,
    interviewerRecordButtonClass: `role-record-button interviewer${roleRecordButtonSuffix}`,
    showErrorKindStrip: Boolean(state.error && state.errorKindText),
    recorderSettingsRequired,
    showRecoveryPanel: Boolean(
      state.recoveryNotice
      || state.canReconnect
      || state.canFinishAfterDisconnect
      || recorderSettingsRequired
    ),
    showRecoveryActions: Boolean(
      state.canReconnect
      || state.canFinishAfterDisconnect
      || recorderSettingsRequired
    ),
    showVoiceAuthorizationPanel: !state.voiceAuthorizationAgreed,
    showAssistantPanel,
    showCompletionIssuePanel,
    showOpeningCompletionOffer: Boolean(state.openingCompletionOffer) && !isHumanInterview && !isBookCompletion
      && !state.liveCharCount && !state.transcriptLines.some((line) => line.role === "user")
      && state.status !== "closed" && state.status !== "error",
    completionIssuePanelClass,
    completionIssueStatusText: completionIssueStatusText(state),
    completionIssueQuestionText: state.completionIssue?.question || "本次问题没有准备好",
    showCompletionIssueActions,
    showCompletionIssueContinue,
    completionIssueControlsDisabled,
    completionIssueActionText: state.completionIssueAction ? "正在处理" : "",
    assistantPanelClass: state.followUpError ? "assistant-panel error-state" : "assistant-panel",
    assistantLabelText: state.followUpError
      ? "小余没说好"
      : (isBookCompletion
        ? (completionRoundFinished ? "本次补充已结束" : "小余正在问这一章")
        : "小余想听您讲讲"),
    showAssistantOpening: !isBookCompletion
      && !hasAssistantPrompt
      && !state.latestTranscriptText
      && !state.followUpLoading
      && !state.followUpError,
    assistantQuestionText: state.assistantPromptText || "今天想先讲哪一段？",
    showAssistantTTS: Boolean(state.followUpTTSPlaying && state.assistantPromptText),
    assistantDisplayText: state.assistantPromptText
      || (state.followUpLoading ? state.assistantLoadingText : (state.followUpError || state.assistantEmptyText)),
    showTTSProgress: Boolean(state.assistantPromptText && state.followUpTTSPlaying),
    showAssistantActions: !state.recording && !completionRoundFinished && showAssistantPanel,
    showTranscriptStage,
    replayButtonText: state.followUpTTSError ? "重试朗读" : (hasAssistantPrompt ? "重听一遍" : "读给我听"),
    followUpButtonText: state.followUpError ? "再问一次" : (state.assistantPromptText ? "换个问法" : "让小余问一句"),
    showStageStatus,
    stageStatusClass: state.error ? "stage-status error" : (state.recording ? "stage-status listening" : "stage-status"),
    stageStatusText,
    liveDisplayText,
    liveCurrentText: liveDisplayText,
    liveTextClass: isHumanInterview && state.recording && !state.liveText ? "live-text placeholder" : "live-text",
    liveScrollClass,
    liveScrollTop: state.liveScrollTop,
    showReturnToLatest,
    returnToLatestText: pendingLiveChars > 0 ? `回到最新 · 新增 ${pendingLiveChars} 字` : "回到最新",
    showCaptureStrip,
    captureStripClass: state.recording ? "capture-strip active" : "capture-strip",
    showLiveCharCount: Boolean(state.recording && state.liveCharCount),
    showInlineHelp: Boolean(!isHumanInterview && state.recording && !state.liveText && state.canRequestFollowUp),
    historyCountText: `${savedNarrationLines.length} 段 · ${savedNarrationCharCount} 字`,
    showHistoryPanel: hasSavedNarration,
    showHistoryLines: state.historyExpanded && hasSavedNarration,
    historyToggleText: state.historyExpanded ? "收起" : "查看",
    transcriptLineViews: previous && previous.transcriptLines === state.transcriptLines
      && previous.interviewType === state.interviewType
      ? previous.transcriptLineViews
      : savedNarrationLines.map((line) => ({
      ...line,
      lineClass: line.role === "assistant"
        ? "transcript-line assistant"
        : (line.role === "interviewer" ? "transcript-line interviewer" : "transcript-line user"),
      roleText: line.role === "assistant"
        ? "小余"
        : (line.role === "interviewer" ? "采访者" : (isHumanInterview ? "被访者" : "我")),
    })),
    controlHintText,
    micButtonClass,
    micButtonDisabled,
    micButtonProcessing: showTranscriptProcessing,
    micButtonText,
  };
}

export function voiceChatAutoListenGateSnapshot(
  data: Pick<
    VoiceChatData,
    "autoListenEnabled" | "status" | "recording" | "followUpLoading"
  >,
  options: {
    hasClient: boolean;
    pendingAfterPlayback: boolean;
    canStart: boolean;
  },
): AutoListenGateSnapshot {
  return {
    hasClient: options.hasClient,
    autoListenEnabled: data.autoListenEnabled,
    status: data.status,
    recording: data.recording,
    followUpLoading: data.followUpLoading,
    pendingAfterPlayback: options.pendingAfterPlayback,
    canStart: options.canStart,
  };
}

type VoiceSessionPatchCurrent = Pick<
  VoiceChatData,
  | "guidanceMode"
  | "currentGuidanceModule"
  | "interviewType"
  | "projectId"
  | "sessionGoal"
  | "currentCompletionIssueId"
  | "transcriptLines"
  | "lastFollowUpSourceMessageId"
  | "followUpLoading"
  | "followUpTTSStateText"
  | "recoveryNotice"
>;

export function voiceChatSessionEntryPatch(options: {
  voiceSessionId: string;
  projectTitle: string;
  guidanceMode: MemoirGuidanceMode;
  currentGuidanceModule: string;
  interviewType: MemoirInterviewType;
  sessionGoal: MemoirSessionGoal;
  voiceAuthorizationAgreed: boolean;
}): VoiceChatPatch {
  const isHumanInterview = options.interviewType === "human_interview";
  return {
    voiceSessionId: options.voiceSessionId,
    projectTitle: options.projectTitle,
    guidanceMode: options.guidanceMode,
    currentGuidanceModule: options.currentGuidanceModule,
    interviewType: options.interviewType,
    sessionGoal: options.sessionGoal,
    voiceAuthorizationAgreed: options.voiceAuthorizationAgreed,
    primaryActionText: options.voiceAuthorizationAgreed ? "开始说话" : "同意后开始说话",
    autoListenEnabled: !isHumanInterview,
    autoListenStateText: isHumanInterview ? "真人访谈不自动追问" : "小余说完自动听",
    canRequestFollowUp: false,
  };
}

export function voiceAuthorizationWaitingPatch(): VoiceChatPatch {
  return {
    statusText: "等待语音授权",
    autoListenStateText: "同意后才会自动收音",
  };
}

export function voiceAuthorizationConsentPatch(
  data: Pick<VoiceChatData, "primaryActionText" | "recording">,
  voiceAuthorizationAgreed: boolean,
): VoiceChatPatch {
  const patch: VoiceChatPatch = { voiceAuthorizationAgreed };
  if (voiceAuthorizationAgreed && data.primaryActionText === "同意后开始说话") {
    patch.primaryActionText = "开始说话";
    patch.error = "";
    patch.autoListenStateText = "小余说完自动听";
  }
  if (!voiceAuthorizationAgreed && !data.recording) {
    patch.primaryActionText = "同意后开始说话";
    patch.autoListenStateText = "同意后才会自动收音";
  }
  return patch;
}

export function voiceAuthorizationChangePatch(
  data: Pick<VoiceChatData, "recording" | "error">,
  voiceAuthorizationAgreed: boolean,
): VoiceChatPatch {
  return {
    voiceAuthorizationAgreed,
    recording: voiceAuthorizationAgreed ? data.recording : false,
    error: voiceAuthorizationAgreed ? "" : data.error,
    primaryActionText: voiceAuthorizationAgreed ? "开始说话" : "同意后开始说话",
    autoListenStateText: voiceAuthorizationAgreed ? "小余说完自动听" : "同意后才会自动收音",
  };
}

export function voiceAuthorizationRequiredPatch(autoTriggered: boolean): VoiceChatPatch {
  return {
    voiceAuthorizationAgreed: false,
    error: "请先阅读并同意《语音采集与处理授权》后再开始录音",
    primaryActionText: "同意后开始说话",
    autoListenStateText: autoTriggered ? "自动收音已暂停，等待语音授权" : "同意后才会自动收音",
  };
}

export function voiceAuthorizationSyncFailedPatch(
  message: string,
  forRecording: boolean = false,
): VoiceChatPatch {
  return {
    error: message,
    primaryActionText: "继续说话",
    autoListenStateText: forRecording ? "录音授权失败" : "语音授权同步失败",
  };
}

function assistantSessionPatch(
  lines: VoiceChatTranscriptLine[],
  guidanceMode: MemoirGuidanceMode,
  current: VoiceSessionPatchCurrent,
): VoiceChatPatch {
  const coveredSourceMessageId = coveredFollowUpSourceMessageId(lines);
  const lastFollowUpSourceMessageId = coveredSourceMessageId || current.lastFollowUpSourceMessageId;
  const isHumanInterview = current.interviewType === "human_interview";
  return {
    guidanceMode,
    ...assistantTranscriptState(lines, {
      guidanceMode,
      lastFollowUpSourceMessageId,
      followUpLoading: current.followUpLoading,
      currentFollowUpTTSStateText: current.followUpTTSStateText,
      canRequestFollowUp: isHumanInterview ? false : undefined,
    }),
    lastFollowUpSourceMessageId,
  };
}

export function voiceSessionSnapshotPatch(
  snapshot: MemoirVoiceSessionSnapshot,
  current: VoiceSessionPatchCurrent,
): VoiceChatPatch {
  const guidanceMode = snapshot.detail?.session.guidance_mode || current.guidanceMode;
  const currentGuidanceModule = snapshot.detail?.session.current_guidance_module || current.currentGuidanceModule;
  const interviewType = snapshot.detail?.session.interview_type || current.interviewType;
  const sessionGoal = snapshot.detail?.session.session_goal || current.sessionGoal;
  const currentCompletionIssueId = snapshot.detail
    ? (snapshot.detail.session.current_completion_issue_id || "")
    : current.currentCompletionIssueId;
  return {
    ...assistantSessionPatch(snapshot.transcriptLines, guidanceMode, {
      ...current,
      currentGuidanceModule,
      interviewType,
    }),
    currentGuidanceModule,
    interviewType,
    projectId: snapshot.detail?.session.project_id || current.projectId,
    sessionGoal,
    currentCompletionIssueId,
    recoveryNotice: snapshot.pendingClose ? "正在恢复上次离开前的讲述记录" : current.recoveryNotice,
  };
}

function voiceSessionStatusPatch(detail: MemoirVoiceSessionDetail): VoiceChatPatch {
  if (detail.session.status === "stopped") {
    return {
      status: "closed",
      statusText: statusText("closed"),
      primaryActionText: "已保存",
      recording: false,
      persistenceStateText: "已保存",
    };
  }
  if (detail.session.status === "failed") {
    return {
      status: "error",
      statusText: statusText("error"),
      primaryActionText: "重新开始",
      recording: false,
      persistenceStateText: "会话异常",
      error: detail.session.error_message || "本次语音保存异常，请返回首页重新开始",
    };
  }
  return {};
}

export function voiceSessionDetailPatch(
  detail: MemoirVoiceSessionDetail,
  current: VoiceSessionPatchCurrent,
): VoiceChatPatch {
  const detailLines = transcriptLinesFromVoiceSessionDetail(detail);
  const guidanceMode = detail.session.guidance_mode || current.guidanceMode;
  const currentGuidanceModule = detail.session.current_guidance_module || current.currentGuidanceModule;
  const interviewType = detail.session.interview_type || current.interviewType;
  const sessionGoal = detail.session.session_goal || current.sessionGoal;
  const nextLines = detailLines.length >= current.transcriptLines.length
    ? detailLines
    : current.transcriptLines;
  return {
    ...voiceSessionStatusPatch(detail),
    ...assistantSessionPatch(nextLines, guidanceMode, {
      ...current,
      currentGuidanceModule,
      interviewType,
    }),
    currentGuidanceModule,
    interviewType,
    projectId: detail.session.project_id || current.projectId,
    sessionGoal,
    currentCompletionIssueId: detail.session.current_completion_issue_id || "",
  };
}

export function voiceConnectionStatusPatch(
  status: VoiceChatConnectionStatus,
  current: Pick<VoiceChatData, "recording">,
): VoiceChatPatch {
  return {
    status,
    statusText: statusText(status),
    recording: status === "listening"
      ? true
      : current.recording && status !== "stopping" && status !== "closed" && status !== "error",
  };
}

export interface VoiceCaptureStatsPatch {
  patch: VoiceChatPatch;
  shouldHandleNoAnswerAutoStop: boolean;
}

export function voiceCaptureStatsPatch(
  stats: MemoirCaptureStats,
  options: {
    noAnswerPromptAlreadyHandled: boolean;
  },
): VoiceCaptureStatsPatch {
  const noAnswerAutoStopped = stats.autoStopped && stats.autoStopReason === "no_voice";
  const shouldHandleNoAnswerAutoStop = noAnswerAutoStopped && !options.noAnswerPromptAlreadyHandled;
  const patch: VoiceChatPatch = {
    captureDurationText: formatDuration(stats.recordingElapsedMs),
    gatewayStateText: gatewayStateText(stats.gatewayState),
    audioLevelPercent: Math.min(100, Math.round(Math.sqrt(stats.audioLevel || 0) * 100)),
  };
  if (shouldHandleNoAnswerAutoStop) {
    patch.autoListenStateText = "没听到回答，已暂停，可点继续说话";
    patch.persistenceStateText = "未听到回答";
    patch.primaryActionText = "继续说话";
  }
  return { patch, shouldHandleNoAnswerAutoStop };
}

export function voiceGatewayEventPatch(
  event: VoiceGatewayServerEvent,
  current: Pick<VoiceChatData, "persistenceStateText">,
): VoiceChatPatch {
  const nextData: VoiceChatPatch = {};
  if (event.state) {
    nextData.gatewayStateText = gatewayStateText(event.state);
  }
  if (event.type === "audio.ended") {
    nextData.recording = false;
    nextData.primaryActionText = "继续说话";
    nextData.persistenceStateText = current.persistenceStateText === "已保存"
      || current.persistenceStateText === "未听到回答"
      || current.persistenceStateText === "小余正在确认"
      ? current.persistenceStateText
      : "正在保存";
  }
  if (event.type === "session.started") {
    nextData.persistenceStateText = "等待识别";
    nextData.liveText = "";
    nextData.liveCharCount = 0;
    nextData.liveScrollClass = "live-scroll";
    nextData.liveScrollTop = 0;
    nextData.liveTranscriptFollowing = true;
    nextData.liveReviewStartCharCount = 0;
  }
  return nextData;
}

export function voiceChatErrorPatch(
  message: string,
  error: VoiceChatErrorInfo | undefined,
  current: Pick<VoiceChatData, "persistenceStateText" | "recoveryNotice">,
): VoiceChatPatch {
  const nextData: VoiceChatPatch = {
    error: message,
    errorKindText: errorKindText(error),
    persistenceStateText: error?.kind === "persistence" ? "保存失败" : current.persistenceStateText,
    status: "error",
    statusText: statusText("error"),
    recording: false,
    primaryActionText: "重新连接",
    recoveryNotice: error?.kind === "connection" ? "语音连接中断，已保留本地讲述记录" : current.recoveryNotice,
    canReconnect: error?.kind === "connection",
    canFinishAfterDisconnect: error?.kind === "connection",
  };
  if (error?.state) {
    nextData.gatewayStateText = gatewayStateText(error.state);
  }
  return nextData;
}
