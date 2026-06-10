import type { MemoirTtsPlaybackState } from "./memoir-tts-player";

export interface MemoirTtsViewPatch {
  ttsStatus: MemoirTtsPlaybackState["status"];
  ttsSourceKey: string;
  ttsMessage: string;
  ttsError: string;
  ttsPlaying: boolean;
  ttsBusy: boolean;
}

export function memoirTtsViewPatch(state: MemoirTtsPlaybackState): MemoirTtsViewPatch {
  return {
    ttsStatus: state.status,
    ttsSourceKey: state.source_key,
    ttsMessage: state.message,
    ttsError: state.error,
    ttsPlaying: state.status === "playing",
    ttsBusy: state.status === "loading" || state.status === "generating",
  };
}
