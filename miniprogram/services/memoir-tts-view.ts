import type { MemoirTtsPlaybackState } from "./memoir-tts-player";
import {
  memoirTtsSettingsLabel,
  memoirTtsSpeedLabel,
  normalizeMemoirTtsVoice,
  memoirTtsVoiceLabel,
} from "./memoir-tts-settings";

export interface MemoirTtsViewPatch {
  ttsStatus: MemoirTtsPlaybackState["status"];
  ttsSourceKey: string;
  ttsMessage: string;
  ttsError: string;
  ttsSpeed: number;
  ttsSpeedLabel: string;
  ttsVoice: string;
  ttsVoiceLabel: string;
  ttsSettingsLabel: string;
  ttsPlaying: boolean;
  ttsBusy: boolean;
}

export function memoirTtsViewPatch(state: MemoirTtsPlaybackState): MemoirTtsViewPatch {
  const voice = normalizeMemoirTtsVoice(state.voice);
  return {
    ttsStatus: state.status,
    ttsSourceKey: state.source_key,
    ttsMessage: state.message,
    ttsError: state.error,
    ttsSpeed: state.speed,
    ttsSpeedLabel: memoirTtsSpeedLabel(state.speed),
    ttsVoice: voice,
    ttsVoiceLabel: memoirTtsVoiceLabel(voice),
    ttsSettingsLabel: memoirTtsSettingsLabel({ voice, speed: state.speed }),
    ttsPlaying: state.status === "playing",
    ttsBusy: state.status === "loading" || state.status === "generating",
  };
}
