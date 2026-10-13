import { AudioPlaybackController } from "../../services/audio-playback";
import { StreamingVoiceController } from "../../services/streaming-voice-controller";
import type { VoiceAssistantTurnRuntime } from "../../services/voice-assistant-turn-runtime";
import type { AssistantTurnQueueItem } from "../../services/voice-assistant-turn-queue";
import { VoiceAssistantTTSProgressRuntime } from "../../services/voice-assistant-tts-progress";
import { PollingTimer } from "../../services/polling-timer";
import { StoryProgressWatcher } from "../../services/story-growth-notice";
import type { VoiceChatCompletionIssueRuntime } from "./voice-chat-completion-issue-runtime";
import type { VoiceSessionExit } from "./events";

export function createVoiceSessionRuntime() {
  return {
    audioPlayback: new AudioPlaybackController(),
    voiceStreaming: new StreamingVoiceController(),
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
    liveTranscriptMeasureVersion: 0,
    liveTranscriptMeasurePending: false,
    followUpRequestSerial: 0,
    autoListenPendingAfterPlayback: false,
    pageSuspended: false,
    sessionStopConfirmed: false,
    gatewayConnectPromise: null as Promise<boolean> | null,
    recordingStartPromise: null as Promise<boolean> | null,
    recordingStartGeneration: 0,
    completionIssueRuntime: null as VoiceChatCompletionIssueRuntime | null,
    storyGrowthWatchToken: 0,
    storyGrowthTimer: new PollingTimer(3000),
    progressNoticeWatcher: new StoryProgressWatcher(),
    disposed: false,
    pendingExit: null as VoiceSessionExit | null,
    requestedExit: null as VoiceSessionExit | null,
  };
}

export type VoiceSessionRuntime = ReturnType<typeof createVoiceSessionRuntime>;
const runtimes = new WeakMap<object, VoiceSessionRuntime>();

export function voiceSessionRuntime(owner: object): VoiceSessionRuntime {
  let runtime = runtimes.get(owner);
  if (!runtime) {
    runtime = createVoiceSessionRuntime();
    runtimes.set(owner, runtime);
  }
  return runtime;
}
