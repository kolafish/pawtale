import type { VoiceChatConnectionStatus } from "./memoir-streaming-asr";

export interface AutoListenState {
  autoListenEnabled: boolean;
  status: VoiceChatConnectionStatus;
  recording: boolean;
  followUpLoading: boolean;
}

export function canAutoStartListeningAfterTTS(state: AutoListenState): boolean {
  if (!state.autoListenEnabled || state.recording || state.followUpLoading) {
    return false;
  }
  return state.status === "ready";
}
