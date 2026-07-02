import type { VoiceChatConnectionStatus } from "../../services/memoir-streaming-asr";
import { statusText } from "../../services/voice-chat-state";
import type { VoiceChatData } from "./voice-chat-page-state";

type VoiceChatPatch = Partial<VoiceChatData>;

export function voiceClientUnavailableForRecording(
  hasClient: boolean,
  status: VoiceChatConnectionStatus,
): boolean {
  return !hasClient || status === "closed" || status === "error";
}

export function voiceRecordingReconnectPatch(
  autoTriggered: boolean,
  currentAutoListenStateText: string,
): VoiceChatPatch {
  return {
    autoListenStateText: autoTriggered ? "正在重新连接语音" : currentAutoListenStateText,
  };
}

export function voiceAssistantInterruptedPatch(): VoiceChatPatch {
  return {
    followUpLoading: false,
    followUpError: "",
    followUpTTSStateText: "正在听您说",
    persistenceStateText: "正在听你说",
  };
}

export function voiceRecorderPrivacyDeniedPatch(
  autoTriggered: boolean,
  currentAutoListenStateText: string,
): VoiceChatPatch {
  return {
    recording: false,
    primaryActionText: "继续说话",
    autoListenStateText: autoTriggered ? "自动收音已暂停，等待隐私授权" : currentAutoListenStateText,
  };
}

export function voiceRecordingUnavailablePatch(
  autoTriggered: boolean,
  currentAutoListenStateText: string,
): VoiceChatPatch {
  return {
    error: "语音未就绪，请重新连接",
    status: "error",
    statusText: statusText("error"),
    primaryActionText: "重新连接",
    canReconnect: true,
    autoListenStateText: autoTriggered ? "自动收音未就绪" : currentAutoListenStateText,
  };
}

export function voiceRecordingStartedPatch(autoTriggered: boolean): VoiceChatPatch {
  return {
    recording: true,
    error: "",
    liveText: "",
    liveCharCount: 0,
    selectedOpeningTopicText: "",
    primaryActionText: "暂停收音",
    autoListenStateText: autoTriggered ? "小余说完，正在听您说" : "小余说完自动听",
  };
}

export function voiceRecordingStoppedPatch(): VoiceChatPatch {
  return {
    recording: false,
    primaryActionText: "继续说话",
  };
}

export interface NoAnswerPromptPlan {
  attempt: number;
  autoListenAfterPlayback: boolean;
}

export function noAnswerPromptPlan(consecutiveNoAnswerPrompts: number, maxPrompts: number): NoAnswerPromptPlan {
  const attempt = Math.min(consecutiveNoAnswerPrompts + 1, maxPrompts);
  return {
    attempt,
    autoListenAfterPlayback: attempt < maxPrompts,
  };
}

export function noAnswerPromptStartPatch(): VoiceChatPatch {
  return {
    followUpLoading: true,
    followUpError: "",
    canRequestFollowUp: false,
    persistenceStateText: "小余正在确认",
    autoListenStateText: "没听到回答，小余再问一句",
  };
}

export function noAnswerPromptQueuedPatch(autoListenAfterPlayback: boolean): VoiceChatPatch {
  return {
    dismissedFollowUpMessageId: "",
    followUpTTSError: "",
    persistenceStateText: autoListenAfterPlayback ? "小余正在确认" : "已暂停收音",
    autoListenStateText: autoListenAfterPlayback ? "小余问完会继续听" : "已暂停，可点继续说话",
  };
}

export function noAnswerPromptFailedPatch(canRequestFollowUp: boolean, message: string): VoiceChatPatch {
  return {
    followUpError: message,
    persistenceStateText: "确认失败",
    autoListenStateText: "没听到回答，可点继续说话",
    canRequestFollowUp,
  };
}

export function finishSessionStartPatch(wasRecording: boolean): VoiceChatPatch {
  return {
    finishingSession: true,
    error: "",
    recording: false,
    status: "stopping",
    statusText: statusText("stopping"),
    primaryActionText: "保存中",
    persistenceStateText: wasRecording ? "正在保存最后一句" : "正在保存",
  };
}

export function finishSessionSavedPatch(): VoiceChatPatch {
  return {
    recording: false,
    primaryActionText: "已保存",
    status: "closed",
    statusText: statusText("closed"),
    persistenceStateText: "正在同步",
  };
}
