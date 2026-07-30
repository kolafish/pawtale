import type { TTSAlignmentSegment } from "../../domain/memoir-audio";
import type { MemoirBookAudioResponse } from "../../domain/memoir-book";
import {
  playAudioFileSequence,
  type AudioPlaybackController,
} from "../../services/audio-playback";
import {
  VoiceAssistantTTSProgressRuntime,
  type VoiceAssistantTTSProgressPatch,
} from "../../services/voice-assistant-tts-progress";
import { positiveFiniteSeconds } from "./book-view-model";

export interface BookTTSState {
  error: string;
  playingTarget: string;
  ttsBusyTarget: string;
  bookTTSActiveTarget: string;
  bookTTSActiveText: string;
  bookTTSSpokenText: string;
  bookTTSCurrentText: string;
  bookTTSRemainingText: string;
  bookTTSProgressPercent: number;
  bookTTSDurationSeconds: number;
  bookTTSAlignment: TTSAlignmentSegment[];
}

export interface BookTTSHost {
  data: BookTTSState;
  setData(patch: Partial<BookTTSState>): void;
  audioPlayback: AudioPlaybackController;
  ttsProgress: VoiceAssistantTTSProgressRuntime;
  playToken: number;
  onBookTTSProgressPatch?(patch: VoiceAssistantTTSProgressPatch): void;
}

export function applyBookTTSProgressPatch(
  host: BookTTSHost,
  patch: VoiceAssistantTTSProgressPatch,
): void {
  host.setData({
    bookTTSSpokenText: patch.ttsSpokenText,
    bookTTSCurrentText: patch.ttsCurrentText,
    bookTTSRemainingText: patch.ttsRemainingText,
    bookTTSProgressPercent: patch.ttsProgressPercent,
  });
  host.onBookTTSProgressPatch?.(patch);
}

export async function playBookAudioFiles(
  host: BookTTSHost,
  response: MemoirBookAudioResponse,
  target: string,
  token: number,
  activeText: string,
): Promise<void> {
  const audioFileIds = response.audio_file_ids || [];
  const durationSeconds = positiveFiniteSeconds(response.duration_seconds);
  const alignment = Array.isArray(response.alignment) ? response.alignment : [];
  host.setData({
    playingTarget: target,
    error: "",
    bookTTSActiveTarget: target,
    bookTTSActiveText: activeText,
    bookTTSDurationSeconds: durationSeconds,
    bookTTSAlignment: alignment,
  });
  updateBookTTSProgress(host, 0, durationSeconds);
  let elapsedBeforeSeconds = 0;
  try {
    const completed = await playAudioFileSequence(audioFileIds, {
      token,
      isCurrent: (candidate) => candidate === host.playToken,
      playFile: async (audioFileId, index) => {
        const fallbackDurationSeconds = positiveFiniteSeconds(
          response.audio_file_durations_seconds?.[index],
        );
        const playedSeconds = await playBookAudioFile(
          host,
          audioFileId,
          token,
          elapsedBeforeSeconds,
          fallbackDurationSeconds,
        );
        elapsedBeforeSeconds += fallbackDurationSeconds || playedSeconds;
      },
    });
    if (completed) {
      updateBookTTSProgress(host, durationSeconds || elapsedBeforeSeconds, durationSeconds || elapsedBeforeSeconds);
    }
  } catch (error) {
    if (token === host.playToken) {
      resetBookTTSProgress(host);
    }
    throw error;
  } finally {
    if (token === host.playToken) {
      host.setData({ playingTarget: "" });
    }
    stopBookTTSProgressTimer(host);
  }
}

export async function playBookAudioFile(
  host: BookTTSHost,
  audioFileId: string,
  token: number,
  elapsedBeforeSeconds: number,
  fallbackDurationSeconds: number,
): Promise<number> {
  if (token !== host.playToken) {
    return 0;
  }
  let playedSeconds = 0;
  const elapsedSeconds = (audio: WechatMiniprogram.InnerAudioContext): number => {
    const duration = fallbackDurationSeconds
      || positiveFiniteSeconds(audio.duration)
      || playbackTimeForProgress(host, audio);
    return positiveFiniteSeconds(duration);
  };
  await host.audioPlayback.playFile(audioFileId, token, {
    onPlay: (audio) => {
      host.ttsProgress.markPlaybackStarted();
      startBookTTSProgressTimer(host, token, audio, elapsedBeforeSeconds, fallbackDurationSeconds);
    },
    onTimeUpdate: (audio) => {
      if (token === host.playToken && host.audioPlayback.currentAudio() === audio) {
        updateBookTTSProgress(
          host,
          elapsedBeforeSeconds + playbackTimeForProgress(host, audio),
          host.data.bookTTSDurationSeconds,
        );
      }
    },
    onEnded: (audio) => {
      const seconds = elapsedSeconds(audio);
      playedSeconds = seconds;
      updateBookTTSProgress(host, elapsedBeforeSeconds + seconds, host.data.bookTTSDurationSeconds);
      stopBookTTSProgressTimer(host);
    },
    onStop: (audio) => {
      playedSeconds = playbackTimeForProgress(host, audio);
      stopBookTTSProgressTimer(host);
    },
    onError: () => {
      stopBookTTSProgressTimer(host);
    },
  });
  return playedSeconds;
}

export function updateBookTTSProgress(
  host: BookTTSHost,
  currentTimeSeconds: number,
  durationSeconds?: number,
): void {
  const activeText = host.data.bookTTSActiveText;
  if (!activeText) {
    return;
  }
  host.ttsProgress.updateAudioFileProgress(
    activeText,
    currentTimeSeconds,
    positiveFiniteSeconds(durationSeconds) || host.data.bookTTSDurationSeconds,
    0,
    host.data.bookTTSAlignment,
    (patch) => applyBookTTSProgressPatch(host, patch),
  );
}

export function startBookTTSProgressTimer(
  host: BookTTSHost,
  token: number,
  audio: WechatMiniprogram.InnerAudioContext,
  elapsedBeforeSeconds: number,
  durationSeconds: number,
): void {
  stopBookTTSProgressTimer(host);
  updateBookTTSProgress(
    host,
    elapsedBeforeSeconds + playbackTimeForProgress(host, audio),
    host.data.bookTTSDurationSeconds || elapsedBeforeSeconds + durationSeconds,
  );
  host.ttsProgress.startAudioFileTimer({
    audio,
    isActive: () => token === host.playToken && host.audioPlayback.currentAudio() === audio,
    currentTimeOffsetSeconds: elapsedBeforeSeconds,
    durationSeconds: () => host.data.bookTTSDurationSeconds || elapsedBeforeSeconds + durationSeconds,
    text: () => host.data.bookTTSActiveText,
    alignment: () => host.data.bookTTSAlignment,
    fallbackDurationSeconds: host.data.bookTTSDurationSeconds || elapsedBeforeSeconds + durationSeconds,
    applyPatch: (patch) => applyBookTTSProgressPatch(host, patch),
  });
}

export function stopBookTTSProgressTimer(host: BookTTSHost): void {
  host.ttsProgress.stopTimer();
}

export function resetBookTTSProgress(host: BookTTSHost): void {
  stopBookTTSProgressTimer(host);
  host.setData({
    bookTTSActiveTarget: "",
    bookTTSActiveText: "",
    bookTTSSpokenText: "",
    bookTTSCurrentText: "",
    bookTTSRemainingText: "",
    bookTTSProgressPercent: 0,
    bookTTSDurationSeconds: 0,
    bookTTSAlignment: [],
  });
}

export function playbackTimeForProgress(
  host: BookTTSHost,
  audio: WechatMiniprogram.InnerAudioContext,
): number {
  return host.ttsProgress.playbackTimeForAudio(audio);
}

export function stopBookPlayback(host: BookTTSHost): void {
  host.audioPlayback.stop();
  host.playToken = host.audioPlayback.currentToken();
  stopBookTTSProgressTimer(host);
  if (host.data.playingTarget || host.data.ttsBusyTarget) {
    host.setData({ playingTarget: "", ttsBusyTarget: "" });
  }
  resetBookTTSProgress(host);
}
