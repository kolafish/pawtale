import type {
  MemoirGuidanceMode,
  MemoirVoiceSessionDetail,
  TTSAlignmentSegment,
  VoiceChatErrorInfo,
  VoiceChatTranscriptLine,
  VoiceGatewayServerEvent,
} from "../../domain/voice-chat";
import { voiceGuidanceLabel } from "../../services/memoir-project-profile";
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
  projectTitle: string;
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
  finishButtonText: string;
  topicTitleText: string;
  showErrorKindStrip: boolean;
  showRecoveryPanel: boolean;
  showRecoveryActions: boolean;
  showVoiceAuthorizationPanel: boolean;
  showAssistantPanel: boolean;
  assistantPanelClass: string;
  assistantLabelText: string;
  showAssistantOpening: boolean;
  assistantQuestionText: string;
  showSelectedOpeningTopic: boolean;
  showAssistantTTS: boolean;
  assistantDisplayText: string;
  showTTSProgress: boolean;
  replayButtonText: string;
  followUpButtonText: string;
  showSkipFollowUp: boolean;
  stageStatusClass: string;
  stageStatusText: string;
  liveDisplayText: string;
  captureStripClass: string;
  showLiveCharCount: boolean;
  showInlineHelp: boolean;
  historyCountText: string;
  showHistoryEmpty: boolean;
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
    projectTitle: "",
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
    assistantPromptLabel: "小余想听您讲讲",
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
    modeLabel: "慢慢讲",
    primaryActionText: "开始说话",
    captureStateText: "麦克风未开始",
    captureFrameCount: 0,
    captureKilobytes: 0,
    captureDurationText: "00:00",
    audioLevelPercent: 0,
    autoStopEnabled: true,
    autoStopStateText: "自动收尾开",
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
    topicTitleText: "这段回忆",
    showErrorKindStrip: false,
    showRecoveryPanel: false,
    showRecoveryActions: false,
    showVoiceAuthorizationPanel: true,
    showAssistantPanel: false,
    assistantPanelClass: "assistant-panel",
    assistantLabelText: "小余想听您讲讲",
    showAssistantOpening: true,
    assistantQuestionText: "今天想先讲哪一段？",
    showSelectedOpeningTopic: false,
    showAssistantTTS: false,
    assistantDisplayText: "小余会先问一句，帮您开始讲。",
    showTTSProgress: false,
    replayButtonText: "读给我听",
    followUpButtonText: "让小余问一句",
    showSkipFollowUp: false,
    stageStatusClass: "stage-status",
    stageStatusText: "可以开始讲了",
    liveDisplayText: "点下方“我来说一段”",
    captureStripClass: "capture-strip",
    showLiveCharCount: false,
    showInlineHelp: false,
    historyCountText: "0 段 · 0 字",
    showHistoryEmpty: true,
    transcriptLineViews: [],
    controlHintText: "说一两句也可以，我会帮您记下来。",
    micButtonClass: "mic-button",
    micButtonDisabled: true,
    micButtonText: "同意后开始说话",
    largeTextEnabled: false,
    autoBargeInEnabled: true,
    voiceChatPageClass: "voice-chat-page",
  };
}

export type VoiceChatPatch = Partial<VoiceChatData>;

export type VoiceChatViewPatch = Pick<
  VoiceChatData,
  | "finishButtonText"
  | "topicTitleText"
  | "showErrorKindStrip"
  | "showRecoveryPanel"
  | "showRecoveryActions"
  | "showVoiceAuthorizationPanel"
  | "showAssistantPanel"
  | "assistantPanelClass"
  | "assistantLabelText"
  | "showAssistantOpening"
  | "assistantQuestionText"
  | "showSelectedOpeningTopic"
  | "showAssistantTTS"
  | "assistantDisplayText"
  | "showTTSProgress"
  | "replayButtonText"
  | "followUpButtonText"
  | "showSkipFollowUp"
  | "stageStatusClass"
  | "stageStatusText"
  | "liveDisplayText"
  | "captureStripClass"
  | "showLiveCharCount"
  | "showInlineHelp"
  | "historyCountText"
  | "showHistoryEmpty"
  | "transcriptLineViews"
  | "controlHintText"
  | "micButtonClass"
  | "micButtonDisabled"
  | "micButtonText"
  | "voiceChatPageClass"
>;

export function voiceChatViewPatch(state: VoiceChatData): VoiceChatViewPatch {
  const assistantBusy = state.followUpLoading || state.followUpTTSLoading || state.followUpTTSPlaying;
  const hasAssistantPrompt = Boolean(state.assistantPromptText);
  const showAssistantPanel = !state.recording
    && Boolean(hasAssistantPrompt || state.followUpLoading || state.followUpError || state.canRequestFollowUp);
  let stageStatusText = "可以开始讲了";
  if (state.error) {
    stageStatusText = "需要处理一下";
  } else if (state.recording) {
    stageStatusText = "我在听，您慢慢说";
  } else if (state.persistenceStateText === "已保存") {
    stageStatusText = "这一段已经记下来了";
  } else if (state.followUpTTSPlaying || (state.followUpTTSLoading && hasAssistantPrompt)) {
    stageStatusText = "小余正在读问题";
  } else if (state.followUpLoading) {
    stageStatusText = state.latestTranscriptText ? "正在帮您整理成文字" : "小余正在准备问题";
  }
  let liveDisplayText = "点下方“我来说一段”";
  if (state.recording) {
    liveDisplayText = state.liveText || "直接说就行，我会帮您记下来";
  } else {
    liveDisplayText = state.liveText
      || state.latestTranscriptText
      || state.assistantPromptText
      || (state.followUpLoading ? "小余正在准备第一句话" : "点下方“我来说一段”");
  }
  let controlHintText = "说一两句也可以，我会帮您记下来。";
  if (state.recording) {
    controlHintText = "说完请点这里";
  } else if (state.followUpLoading && !hasAssistantPrompt) {
    controlHintText = "小余先问一句，稍等一下。";
  } else if (assistantBusy) {
    controlHintText = "点这里就可以开始说";
  }
  let micButtonText = state.primaryActionText;
  if (!state.voiceAuthorizationAgreed) {
    micButtonText = "同意后开始说话";
  } else if (state.connecting) {
    micButtonText = "正在准备";
  } else if (state.recording) {
    micButtonText = "说完了，帮我整理";
  } else if (assistantBusy) {
    micButtonText = "我来讲了";
  } else if (state.primaryActionText === "开始说话") {
    micButtonText = "我来说一段";
  } else if (state.primaryActionText === "继续说话") {
    micButtonText = "继续讲";
  }
  return {
    voiceChatPageClass: state.largeTextEnabled ? "voice-chat-page large-text" : "voice-chat-page",
    finishButtonText: state.finishingSession ? "保存中" : "保存离开",
    topicTitleText: state.projectTitle || "这段回忆",
    showErrorKindStrip: Boolean(state.error && state.errorKindText),
    showRecoveryPanel: Boolean(state.recoveryNotice || state.canReconnect || state.canFinishAfterDisconnect),
    showRecoveryActions: Boolean(state.canReconnect || state.canFinishAfterDisconnect),
    showVoiceAuthorizationPanel: !state.voiceAuthorizationAgreed,
    showAssistantPanel,
    assistantPanelClass: state.followUpError ? "assistant-panel error-state" : "assistant-panel",
    assistantLabelText: state.followUpError ? "小余没说好" : "小余想听您讲讲",
    showAssistantOpening: !state.latestTranscriptText
      && !state.followUpLoading
      && !state.followUpError
      && !state.selectedOpeningTopicText,
    assistantQuestionText: state.assistantPromptText || "今天想先讲哪一段？",
    showSelectedOpeningTopic: Boolean(state.selectedOpeningTopicText),
    showAssistantTTS: Boolean(state.followUpTTSPlaying && state.assistantPromptText),
    assistantDisplayText: state.assistantPromptText
      || (state.followUpLoading ? state.assistantLoadingText : (state.followUpError || state.assistantEmptyText)),
    showTTSProgress: Boolean(state.assistantPromptText && state.followUpTTSPlaying),
    replayButtonText: state.followUpAudioFileId ? "重听一遍" : (state.followUpTTSError ? "重试朗读" : "读给我听"),
    followUpButtonText: state.followUpError ? "再问一次" : (state.assistantPromptText ? "换个问法" : "让小余问一句"),
    showSkipFollowUp: Boolean(state.assistantPromptText && !state.followUpLoading && !state.followUpError),
    stageStatusClass: state.error ? "stage-status error" : (state.recording ? "stage-status listening" : "stage-status"),
    stageStatusText,
    liveDisplayText,
    captureStripClass: state.recording ? "capture-strip active" : "capture-strip",
    showLiveCharCount: Boolean(state.recording && state.liveCharCount),
    showInlineHelp: Boolean(state.recording && !state.liveText && state.canRequestFollowUp),
    historyCountText: `${state.transcriptLines.length} 段 · ${state.totalTranscriptCharCount} 字`,
    showHistoryEmpty: state.transcriptLines.length === 0,
    transcriptLineViews: state.transcriptLines.map((line) => ({
      ...line,
      lineClass: line.role === "assistant" ? "transcript-line assistant" : "transcript-line user",
      roleText: line.role === "assistant" ? "小余" : "我",
    })),
    controlHintText,
    micButtonClass: state.recording ? "mic-button recording" : "mic-button",
    micButtonDisabled: state.connecting || !state.voiceAuthorizationAgreed,
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
  voiceAuthorizationAgreed: boolean;
}): VoiceChatPatch {
  return {
    voiceSessionId: options.voiceSessionId,
    projectTitle: options.projectTitle,
    guidanceMode: options.guidanceMode,
    currentGuidanceModule: options.currentGuidanceModule,
    modeLabel: voiceGuidanceLabel(options.guidanceMode, options.currentGuidanceModule),
    voiceAuthorizationAgreed: options.voiceAuthorizationAgreed,
    primaryActionText: options.voiceAuthorizationAgreed ? "开始说话" : "同意后开始说话",
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

export function voiceAuthorizationSyncFailedPatch(message: string): VoiceChatPatch {
  return {
    error: message,
    primaryActionText: "继续说话",
    autoListenStateText: "声纹授权同步失败",
  };
}

function assistantSessionPatch(
  lines: VoiceChatTranscriptLine[],
  guidanceMode: MemoirGuidanceMode,
  current: VoiceSessionPatchCurrent,
): VoiceChatPatch {
  const coveredSourceMessageId = coveredFollowUpSourceMessageId(lines);
  const lastFollowUpSourceMessageId = coveredSourceMessageId || current.lastFollowUpSourceMessageId;
  return {
    guidanceMode,
    modeLabel: voiceGuidanceLabel(guidanceMode, current.currentGuidanceModule),
    ...assistantTranscriptState(lines, {
      dismissedFollowUpMessageId: current.dismissedFollowUpMessageId,
      guidanceMode,
      lastFollowUpSourceMessageId,
      followUpLoading: current.followUpLoading,
      currentFollowUpTTSStateText: current.followUpTTSStateText,
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
  return {
    ...assistantSessionPatch(snapshot.transcriptLines, guidanceMode, {
      ...current,
      currentGuidanceModule,
    }),
    currentGuidanceModule,
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
  const nextLines = detailLines.length >= current.transcriptLines.length
    ? detailLines
    : current.transcriptLines;
  return {
    ...voiceSessionStatusPatch(detail),
    ...assistantSessionPatch(nextLines, guidanceMode, {
      ...current,
      currentGuidanceModule,
    }),
    currentGuidanceModule,
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
    autoStopEnabled: boolean;
    noAnswerPromptAlreadyHandled: boolean;
  },
): VoiceCaptureStatsPatch {
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
  } else if (!options.autoStopEnabled) {
    autoStopStateText = "自动收尾关";
  } else if (stats.autoStopped) {
    autoStopStateText = "检测到停顿，已自动收尾";
  }
  const noAnswerAutoStopped = stats.autoStopped && stats.autoStopReason === "no_voice";
  const shouldHandleNoAnswerAutoStop = noAnswerAutoStopped && !options.noAnswerPromptAlreadyHandled;
  const patch: VoiceChatPatch = {
    captureStateText: labels[stats.state],
    captureFrameCount: stats.frameCount,
    captureKilobytes: Math.round(stats.audioBytesSent / 1024),
    captureDurationText: formatDuration(stats.recordingElapsedMs),
    gatewayStateText: gatewayStateText(stats.gatewayState),
    audioLevelPercent: Math.min(100, Math.round(Math.sqrt(stats.audioLevel || 0) * 100)),
    autoStopStateText,
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
    captureStateText: "录音异常",
    recoveryNotice: error?.kind === "connection" ? "语音连接中断，已保留本地讲述记录" : current.recoveryNotice,
    canReconnect: error?.kind === "connection",
    canFinishAfterDisconnect: error?.kind === "connection",
  };
  if (error?.state) {
    nextData.gatewayStateText = gatewayStateText(error.state);
  }
  return nextData;
}
