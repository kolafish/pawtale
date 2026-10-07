import type {
  MemoirGuidanceMode,
  VoiceChatTranscriptLine,
  VoiceGatewayServerEvent,
} from "../../domain/memoir-voice-session";
import { normalizeMemoirAsrText } from "../../services/asr-text-normalization";
import type { VoiceChatConnectionStatus } from "../../services/memoir-streaming-asr";
import {
  assistantTranscriptState,
  countTextChars,
  statusText,
  upsertTranscriptLine,
} from "../../services/voice-chat-state";
import type { VoiceChatData } from "./voice-chat-page-state";

type VoiceChatPatch = Partial<VoiceChatData>;

const LIVE_TRANSCRIPT_LONG_TEXT_THRESHOLD = 44;

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
    error: "",
    recordingStarting: true,
    persistenceStateText: "正在准备收音",
    autoListenStateText: autoTriggered ? "正在重新连接语音" : currentAutoListenStateText,
  };
}

export function voiceRecordingStartRequestedPatch(): VoiceChatPatch {
  return {
    recordingStarting: true,
    error: "",
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

export function voiceRecordingUnavailablePatch(
  autoTriggered: boolean,
  currentAutoListenStateText: string,
): VoiceChatPatch {
  return {
    error: "语音未就绪，请重新连接",
    recordingStarting: false,
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
    recordingStarting: false,
    error: "",
    liveText: "",
    liveCharCount: 0,
    ...resetLiveTranscriptScrollPatch(),
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

function resetLiveTranscriptScrollPatch(): VoiceChatPatch {
  return {
    liveScrollClass: "live-scroll",
    liveScrollTop: 0,
    liveTranscriptFollowing: true,
    liveReviewStartCharCount: 0,
  };
}

export function liveTranscriptScrollTop(contentHeight: number, viewportHeight: number): number {
  if (!Number.isFinite(contentHeight) || !Number.isFinite(viewportHeight) || viewportHeight <= 0) {
    return 0;
  }
  return Math.max(0, Math.ceil(contentHeight - viewportHeight));
}

export function voiceLiveTranscriptPatch(text: string): VoiceChatPatch {
  const displayText = normalizeMemoirAsrText(text || "");
  if (!displayText) {
    return {
      liveText: "",
      liveCharCount: 0,
      ...resetLiveTranscriptScrollPatch(),
      persistenceStateText: "识别中",
      error: "",
      errorKindText: "",
    };
  }
  return {
    liveText: displayText,
    liveCharCount: countTextChars(displayText),
    liveScrollClass: Array.from(displayText).length > LIVE_TRANSCRIPT_LONG_TEXT_THRESHOLD
      ? "live-scroll long"
      : "live-scroll",
    persistenceStateText: "识别中",
    error: "",
    errorKindText: "",
  };
}

export function voiceTranscriptReviewStartedPatch(
  current: Pick<VoiceChatData, "liveCharCount" | "liveTranscriptFollowing">,
): VoiceChatPatch {
  if (!current.liveTranscriptFollowing) {
    return {};
  }
  return {
    liveTranscriptFollowing: false,
    liveReviewStartCharCount: current.liveCharCount,
  };
}

export function voiceTranscriptFollowLatestPatch(): VoiceChatPatch {
  return {
    liveTranscriptFollowing: true,
    liveReviewStartCharCount: 0,
  };
}

export interface VoiceFinalTranscriptResult {
  line: VoiceChatTranscriptLine;
  transcriptLines: VoiceChatTranscriptLine[];
  patch: VoiceChatPatch;
}

function savedTranscriptStateText(event: VoiceGatewayServerEvent): string {
  return event.message_id && event.utterance_id && event.transcript_version_id
    ? "已保存"
    : "待同步";
}

function voiceFinalTranscriptLine(
  event: VoiceGatewayServerEvent,
  fallbackId: string,
): VoiceChatTranscriptLine | null {
  const text = normalizeMemoirAsrText(event.text || "");
  if (!text) {
    return null;
  }
  return {
    id: event.message_id || event.utterance_id || fallbackId,
    text,
    role: event.speaker_role === "interviewer" ? "interviewer" : "user",
    message_id: event.message_id,
    utterance_id: event.utterance_id,
    audio_file_id: event.audio_file_id,
    transcript_version_id: event.transcript_version_id,
    start_ms: event.start_ms,
    end_ms: event.end_ms,
  };
}

export function voiceFinalTranscriptResult(
  event: VoiceGatewayServerEvent,
  current: {
    transcriptLines: VoiceChatTranscriptLine[];
    guidanceMode: MemoirGuidanceMode;
    lastFollowUpSourceMessageId: string;
  },
  fallbackId: string,
): VoiceFinalTranscriptResult | null {
  const line = voiceFinalTranscriptLine(event, fallbackId);
  if (!line) {
    return null;
  }
  const transcriptLines = upsertTranscriptLine(current.transcriptLines, line);
  return {
    line,
    transcriptLines,
    patch: {
      liveText: "",
      liveCharCount: 0,
      ...resetLiveTranscriptScrollPatch(),
      ...assistantTranscriptState(transcriptLines, {
        guidanceMode: current.guidanceMode,
        lastFollowUpSourceMessageId: current.lastFollowUpSourceMessageId,
        followUpLoading: false,
        assistantPromptText: "",
        followUpMessageId: "",
        followUpAudioFileId: "",
        followUpTTSStateText: "",
        ttsAlignment: [],
      }),
      persistenceStateText: savedTranscriptStateText(event),
      error: "",
      errorKindText: "",
      assistantPromptText: "",
      selectedOpeningTopicText: "",
      followUpMessageId: "",
      followUpAudioFileId: "",
      followUpLoading: false,
      followUpTTSLoading: false,
      followUpTTSError: "",
    },
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
    recordingStarting: false,
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
