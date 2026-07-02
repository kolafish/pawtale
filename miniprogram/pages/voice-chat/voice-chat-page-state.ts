import type {
  MemoirGuidanceMode,
  TTSAlignmentSegment,
  VoiceChatTranscriptLine,
} from "../../domain/voice-chat";
import type { VoiceChatConnectionStatus } from "../../services/memoir-streaming-asr";

export interface OpeningTopicOption {
  id: string;
  label: string;
  promptText: string;
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
