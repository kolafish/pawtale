import type { TTSAlignmentSegment } from "../../domain/memoir-audio";
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

export interface OpeningTopicOption {
  id: string;
  label: string;
  promptText: string;
}

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
  completionIssueLoading: boolean;
  completionIssueAction: string;
  completionIssueError: string;
  completionIssueNotice: string;
  speakerRole: MemoirSpeakerRole;
  status: VoiceChatConnectionStatus;
  statusText: string;
  recording: boolean;
  recordingStarting: boolean;
  connecting: boolean;
  error: string;
  liveText: string;
  latestTranscriptText: string;
  transcriptLines: VoiceChatTranscriptLine[];
  assistantPromptText: string;
  assistantLoadingText: string;
  assistantEmptyText: string;
  openingTopicOptions: OpeningTopicOption[];
  selectedOpeningTopicText: string;
  assistantExpectsAnswer: boolean;
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
  finishButtonText: string;
  storyGrowthNoticeState: "hidden" | "pending" | "ready" | "failed" | "needs_input" | "no_change";
  storyGrowthNoticeTitle: string;
  storyGrowthNoticeDetail: string;
  storyGrowthNoticeActionText: string;
  progressNoticeTitle: string;
  progressNoticeDetail: string;
  narrationMode: NarrationMode;
  showNarrationModes: boolean;
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
  showSelectedOpeningTopic: boolean;
  showAssistantTTS: boolean;
  assistantDisplayText: string;
  showTTSProgress: boolean;
  showAssistantActions: boolean;
  replayButtonText: string;
  followUpButtonText: string;
  showSkipFollowUp: boolean;
  showStageStatus: boolean;
  stageStatusClass: string;
  stageStatusText: string;
  liveDisplayText: string;
  liveContextText: string;
  liveCurrentText: string;
  liveTextClass: string;
  liveScrollClass: string;
  liveScrollTarget: string;
  liveScrollTop: number;
  liveTextEndId: string;
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
  connecting: boolean;
  followUpLoading: boolean;
  pendingAfterPlayback: boolean;
  canStart: boolean;
}

export function initialVoiceChatData(): VoiceChatData {
  return {
    voiceSessionId: "",
    projectId: "",
    projectTitle: "",
    guidanceMode: "free_narration",
    currentGuidanceModule: "",
    interviewType: "ai_interview",
    sessionGoal: "open_narration",
    currentCompletionIssueId: "",
    completionIssue: null,
    completionIssueLoading: false,
    completionIssueAction: "",
    completionIssueError: "",
    completionIssueNotice: "",
    speakerRole: "narrator",
    status: "idle",
    statusText: "未连接",
    recording: false,
    recordingStarting: false,
    connecting: false,
    error: "",
    liveText: "",
    latestTranscriptText: "",
    transcriptLines: [],
    assistantPromptText: "",
    assistantLoadingText: "小余正在准备第一句话",
    assistantEmptyText: "小余会先问一句，帮您开始讲。",
    openingTopicOptions: [
      { id: "family", label: "家人", promptText: "那我们先讲家人。可以说一件您想到的家里的小事。" },
      { id: "work", label: "工作", promptText: "那我们先讲工作。可以说说那时候每天最忙的一件事。" },
      { id: "friends", label: "朋友", promptText: "那我们先讲朋友。可以说一个您现在还记得的人。" },
      { id: "place", label: "一个地方", promptText: "那我们先讲一个地方。可以说说您最先想到的是哪里。" },
      { id: "free", label: "随便聊聊", promptText: "那就想到哪儿说到哪儿。先说一件刚想起来的小事就好。" },
    ],
    selectedOpeningTopicText: "",
    assistantExpectsAnswer: true,
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
    finishButtonText: "保存离开",
    storyGrowthNoticeState: "hidden",
    storyGrowthNoticeTitle: "",
    storyGrowthNoticeDetail: "",
    storyGrowthNoticeActionText: "",
    progressNoticeTitle: "",
    progressNoticeDetail: "",
    narrationMode: "free_narration",
    showNarrationModes: false,
    topModeLabel: NARRATION_MODES.free_narration.label,
    topSubtitle: "按自己的节奏讲述",
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
    showSelectedOpeningTopic: false,
    showAssistantTTS: false,
    assistantDisplayText: "小余会先问一句，帮您开始讲。",
    showTTSProgress: false,
    showAssistantActions: true,
    replayButtonText: "读给我听",
    followUpButtonText: "让小余问一句",
    showSkipFollowUp: false,
    showStageStatus: true,
    stageStatusClass: "stage-status",
    stageStatusText: "可以开始讲了",
    liveDisplayText: "点下方“我来说一段”",
    liveContextText: "",
    liveCurrentText: "点下方“我来说一段”",
    liveTextClass: "live-text",
    liveScrollClass: "live-scroll",
    liveScrollTarget: "",
    liveScrollTop: 0,
    liveTextEndId: "live-text-end",
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
    controlHintText: "说一两句也可以，我会帮您记下来。",
    micButtonClass: "mic-button disabled",
    micButtonDisabled: true,
    micButtonText: "同意后开始说话",
    largeTextEnabled: false,
    autoBargeInEnabled: true,
    voiceChatPageClass: "voice-chat-page",
  };
}

export type VoiceChatPatch = Partial<VoiceChatData>;

export interface VoiceChatDataContext {
  data: VoiceChatData;
  setData(patch: VoiceChatPatch): void;
}

export function applyVoiceChatDataPatch(
  context: VoiceChatDataContext,
  patch: VoiceChatPatch,
): void {
  const nextData: VoiceChatData = { ...context.data, ...patch };
  const values = {
    ...patch,
    ...voiceChatViewPatch(nextData, context.data),
  };
  const changed = Object.fromEntries(
    Object.entries(values).filter(([key, value]) => !Object.is(context.data[key as keyof VoiceChatData], value)),
  ) as VoiceChatPatch;
  if (Object.keys(changed).length) context.setData(changed);
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
  | "showRecoveryPanel"
  | "showRecoveryActions"
  | "showVoiceAuthorizationPanel"
  | "showAssistantPanel"
  | "showCompletionIssuePanel"
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
  | "showSelectedOpeningTopic"
  | "showAssistantTTS"
  | "assistantDisplayText"
  | "showTTSProgress"
  | "showAssistantActions"
  | "replayButtonText"
  | "followUpButtonText"
  | "showSkipFollowUp"
  | "showStageStatus"
  | "stageStatusClass"
  | "stageStatusText"
  | "liveDisplayText"
  | "liveContextText"
  | "liveCurrentText"
  | "liveTextClass"
  | "liveScrollClass"
  | "liveScrollTarget"
  | "liveScrollTop"
  | "liveTextEndId"
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

function liveTranscriptParts(
  displayText: string,
  state: Pick<VoiceChatData, "recording" | "liveText">,
): { context: string; current: string } {
  if (!state.recording || !state.liveText) {
    return { context: "", current: displayText };
  }
  const characters = Array.from(state.liveText);
  const currentTargetLength = 36;
  const maximumCurrentLength = 56;
  if (characters.length <= maximumCurrentLength) {
    return { context: "", current: state.liveText };
  }
  const preferredSplit = characters.length - currentTargetLength;
  const earliestSplit = Math.max(0, characters.length - maximumCurrentLength);
  let splitAt = preferredSplit;
  for (let index = preferredSplit - 1; index >= earliestSplit; index -= 1) {
    if (/[。！？!?；;，,、\n]/.test(characters[index])) {
      splitAt = index + 1;
      break;
    }
  }
  return {
    context: characters.slice(0, splitAt).join(""),
    current: characters.slice(splitAt).join(""),
  };
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

export function voiceChatViewPatch(state: VoiceChatData, previous?: VoiceChatData): VoiceChatViewPatch {
  const assistantBusy = state.followUpLoading || state.followUpTTSLoading || state.followUpTTSPlaying;
  const hasAssistantPrompt = Boolean(state.assistantPromptText);
  const isHumanInterview = state.interviewType === "human_interview";
  const isBookCompletion = state.sessionGoal === "book_completion";
  const completionRoundFinished = bookCompletionRoundFinished(state);
  const completionUnavailable = isBookCompletion
    && !state.completionIssueLoading
    && !state.completionIssue;
  const showCompletionIssuePanel = isBookCompletion;
  const showAssistantPanel = !isHumanInterview
    && !state.recording
    && Boolean(hasAssistantPrompt || state.followUpLoading || state.followUpError || state.canRequestFollowUp);
  const showHumanRecordActions = isHumanInterview
    && !completionRoundFinished
    && !state.recording
    && !state.recordingStarting
    && state.voiceAuthorizationAgreed;
  const showControlBar = !showHumanRecordActions && !completionRoundFinished;
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
  } else if (state.recording) {
    stageStatusText = isHumanInterview ? `正在记录${speakerRoleLabel(state.speakerRole)}` : "我在听，您慢慢说";
  } else if (state.persistenceStateText === "已保存") {
    stageStatusText = "这一段已经记下来了";
  } else if (state.followUpTTSPlaying || (state.followUpTTSLoading && hasAssistantPrompt)) {
    stageStatusText = "小余正在读问题";
  } else if (state.followUpLoading) {
    stageStatusText = state.latestTranscriptText ? "正在帮您整理成文字" : "小余正在准备问题";
  }
  let liveDisplayText = isHumanInterview ? `点上方开始记录${speakerRoleLabel(state.speakerRole)}` : "点下方“我来说一段”";
  if (state.recording) {
    liveDisplayText = state.liveText || (isHumanInterview ? "等待转写..." : "直接说就行，我会帮您记下来");
  } else if (state.followUpLoading && state.latestTranscriptText) {
    liveDisplayText = "刚才那段正在整理，请稍等";
  } else if (state.followUpLoading && !hasAssistantPrompt) {
    liveDisplayText = "小余正在准备第一句话";
  } else if (state.followUpTTSLoading || state.followUpTTSPlaying || hasAssistantPrompt || state.selectedOpeningTopicText) {
    liveDisplayText = "听完后，点下方“我来讲了”开始回答";
  } else {
    liveDisplayText = state.liveText
      || state.latestTranscriptText
      || (state.followUpLoading ? "小余正在准备第一句话" : (
        isHumanInterview ? `点上方开始记录${speakerRoleLabel(state.speakerRole)}` : "点下方“我来说一段”"
      ));
  }
  if (isBookCompletion && !state.recording && !assistantBusy) {
    liveDisplayText = completionRoundFinished
      ? "回答已经保存，可以返回全书概览。"
      : (
        state.latestTranscriptText
        || (state.completionIssue
          ? "按您记得的顺序讲一段就好"
          : (state.completionIssueLoading ? "正在准备要补的问题" : "请保存离开后，从首页重新进入"))
      );
  }
  const liveTextParts = liveTranscriptParts(liveDisplayText, state);
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
    : (isBookCompletion ? "只说您记得的部分就可以。" : "说一两句也可以，我会帮您记下来。");
  if (state.recording) {
    controlHintText = isHumanInterview ? "" : "说完请点这里";
  } else if (!isHumanInterview && state.followUpLoading && !hasAssistantPrompt) {
    controlHintText = "小余先问一句，稍等一下。";
  } else if (!isHumanInterview && assistantBusy) {
    controlHintText = "点这里就可以开始说";
  }
  if (completionRoundFinished) {
    controlHintText = "";
  }
  let micButtonText = state.primaryActionText;
  if (!state.voiceAuthorizationAgreed) {
    micButtonText = "同意后开始说话";
  } else if (state.recordingStarting) {
    micButtonText = "正在打开麦克风";
  } else if (state.connecting) {
    micButtonText = "正在准备";
  } else if (completionUnavailable && !state.recording) {
    micButtonText = "请先保存离开";
  } else if (state.recording) {
    micButtonText = isHumanInterview ? "说完了，保存这段" : "说完了，帮我整理";
  } else if (!isHumanInterview && assistantBusy) {
    micButtonText = "我来讲了";
  } else if (state.primaryActionText === "开始说话") {
    micButtonText = isHumanInterview ? `记录${speakerRoleLabel(state.speakerRole)}` : "我来说一段";
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
    || !state.voiceAuthorizationAgreed
    || completionRoundFinished
    || (completionUnavailable && !state.recording);
  const roleRecordButtonSuffix = micButtonDisabled ? " disabled" : "";
  const micButtonClass = [
    "mic-button",
    state.recording ? "recording" : "",
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
  return {
    narrationMode,
    voiceChatPageClass: pageClass.filter(Boolean).join(" "),
    finishButtonText: state.finishingSession ? "保存中" : "保存离开",
    topModeLabel: NARRATION_MODES[narrationMode].label,
    topSubtitle: isBookCompletion
      ? "一次只补一个重点"
      : (isHumanInterview
        ? "实时记录采访者和被访者"
        : (state.guidanceMode === "guided_interview" ? "AI 按话题提问，帮助您展开回忆" : "按自己的节奏讲述")),
    topicTitleText: state.projectTitle || "这段回忆",
    showHumanRecordActions,
    showControlBar,
    narratorRecordButtonClass: `role-record-button narrator${roleRecordButtonSuffix}`,
    interviewerRecordButtonClass: `role-record-button interviewer${roleRecordButtonSuffix}`,
    showErrorKindStrip: Boolean(state.error && state.errorKindText),
    showRecoveryPanel: Boolean(state.recoveryNotice || state.canReconnect || state.canFinishAfterDisconnect),
    showRecoveryActions: Boolean(state.canReconnect || state.canFinishAfterDisconnect),
    showVoiceAuthorizationPanel: !state.voiceAuthorizationAgreed,
    showAssistantPanel,
    showCompletionIssuePanel,
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
      && !state.latestTranscriptText
      && !state.followUpLoading
      && !state.followUpError
      && !state.selectedOpeningTopicText,
    assistantQuestionText: state.assistantPromptText || "今天想先讲哪一段？",
    showSelectedOpeningTopic: Boolean(state.selectedOpeningTopicText),
    showAssistantTTS: Boolean(state.followUpTTSPlaying && state.assistantPromptText),
    assistantDisplayText: state.assistantPromptText
      || (state.followUpLoading ? state.assistantLoadingText : (state.followUpError || state.assistantEmptyText)),
    showTTSProgress: Boolean(state.assistantPromptText && state.followUpTTSPlaying),
    showAssistantActions: !completionRoundFinished,
    replayButtonText: state.followUpAudioFileId ? "重听一遍" : (state.followUpTTSError ? "重试朗读" : "读给我听"),
    followUpButtonText: state.followUpError ? "再问一次" : (state.assistantPromptText ? "换个问法" : "让小余问一句"),
    showSkipFollowUp: !isBookCompletion
      && Boolean(state.assistantPromptText && !state.followUpLoading && !state.followUpError),
    showStageStatus,
    stageStatusClass: state.error ? "stage-status error" : (state.recording ? "stage-status listening" : "stage-status"),
    stageStatusText,
    liveDisplayText,
    liveContextText: liveTextParts.context,
    liveCurrentText: liveTextParts.current,
    liveTextClass: isHumanInterview && state.recording && !state.liveText ? "live-text placeholder" : "live-text",
    liveScrollClass,
    liveScrollTarget: state.liveScrollTarget,
    liveScrollTop: state.liveScrollTop,
    liveTextEndId: state.liveTextEndId,
    showReturnToLatest,
    returnToLatestText: pendingLiveChars > 0 ? `回到最新 · 新增 ${pendingLiveChars} 字` : "回到最新",
    showCaptureStrip,
    captureStripClass: state.recording ? "capture-strip active" : "capture-strip",
    showLiveCharCount: Boolean(state.recording && state.liveCharCount),
    showInlineHelp: Boolean(!isHumanInterview && state.recording && !state.liveText && state.canRequestFollowUp),
    historyCountText: `${state.transcriptLines.length} 段 · ${state.totalTranscriptCharCount} 字`,
    showHistoryPanel: state.transcriptLines.length > 0,
    showHistoryLines: state.historyExpanded && state.transcriptLines.length > 0,
    historyToggleText: state.historyExpanded ? "收起" : "查看",
    transcriptLineViews: previous && previous.transcriptLines === state.transcriptLines
      && previous.interviewType === state.interviewType
      ? previous.transcriptLineViews
      : state.transcriptLines.map((line) => ({
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
    micButtonText,
  };
}

export function voiceChatAutoListenGateSnapshot(
  data: Pick<
    VoiceChatData,
    "autoListenEnabled" | "status" | "recording" | "connecting" | "followUpLoading"
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
    connecting: data.connecting,
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
  | "dismissedFollowUpMessageId"
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
    statusText: "等待声纹授权",
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
    error: "请先阅读并同意《声纹授权协议》后再开始录音",
    primaryActionText: "同意后开始说话",
    autoListenStateText: autoTriggered ? "自动收音已暂停，等待声纹授权" : "同意后才会自动收音",
  };
}

export function voiceAuthorizationSyncFailedPatch(
  message: string,
  forRecording: boolean = false,
): VoiceChatPatch {
  return {
    error: message,
    primaryActionText: "继续说话",
    autoListenStateText: forRecording ? "录音授权失败" : "声纹授权同步失败",
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
      dismissedFollowUpMessageId: current.dismissedFollowUpMessageId,
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
    nextData.liveScrollTarget = "";
    nextData.liveScrollTop = 0;
    nextData.liveTextEndId = "live-text-end";
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
