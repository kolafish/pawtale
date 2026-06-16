import type { VoiceChatConnectionStatus } from "./memoir-streaming-asr";

export interface AutoListenState {
  autoListenEnabled: boolean;
  status: VoiceChatConnectionStatus;
  recording: boolean;
  connecting: boolean;
  followUpLoading: boolean;
}

export function canAutoStartListeningAfterTTS(state: AutoListenState): boolean {
  if (!state.autoListenEnabled || state.recording || state.connecting || state.followUpLoading) {
    return false;
  }
  return state.status === "ready";
}
