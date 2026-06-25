import type {
  MemoirBookAudioResponse,
} from "../../domain/voice-chat";
import type { AudioPlaybackController } from "../../services/audio-playback";
import { splitTextByAudioPlaybackProgress } from "../../services/tts-text-progress";
import type { BookData } from "./book-view-model";
import { positiveFiniteSeconds } from "./book-view-model";

export interface BookTTSHost {
  data: Pick<
    BookData,
    | "bookTTSActiveText"
    | "bookTTSDurationSeconds"
    | "bookTTSAlignment"
    | "playingTarget"
    | "ttsBusyTarget"
  >;
  setData(patch: Partial<BookData>): void;
  audioPlayback: AudioPlaybackController;
  playToken: number;
  ttsProgressTimer: number | null;
  ttsProgressStartedAtMs: number;
}

export async function playBookAudioFiles(
  host: BookTTSHost,
  response: MemoirBookAudioResponse,
  target: string,
  token: number,
  activeText: string,
): Promise<void> {
  const audioFileIds = response.audio_file_ids || [];
  if (!audioFileIds.length) {
    throw new Error("没有可播放的音频");
  }
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
    for (let index = 0; index < audioFileIds.length; index += 1) {
      if (token !== host.playToken) {
        return;
      }
      const fallbackDurationSeconds = positiveFiniteSeconds(response.audio_file_durations_seconds?.[index]);
      const playedSeconds = await playBookAudioFile(
        host,
        audioFileIds[index],
        token,
        elapsedBeforeSeconds,
        fallbackDurationSeconds,
      );
      elapsedBeforeSeconds += fallbackDurationSeconds || playedSeconds;
    }
    if (token === host.playToken) {
      updateBookTTSProgress(host, durationSeconds || elapsedBeforeSeconds, durationSeconds || elapsedBeforeSeconds);
    }
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
      host.ttsProgressStartedAtMs = Date.now();
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
  const progress = splitTextByAudioPlaybackProgress(
    activeText,
    currentTimeSeconds,
    positiveFiniteSeconds(durationSeconds) || host.data.bookTTSDurationSeconds,
    { alignment: host.data.bookTTSAlignment },
  );
  host.setData({
    bookTTSSpokenText: progress.spokenText,
    bookTTSCurrentText: progress.currentText,
    bookTTSRemainingText: progress.remainingText,
    bookTTSProgressPercent: progress.progressPercent,
  });
}

export function startBookTTSProgressTimer(
  host: BookTTSHost,
  token: number,
  audio: WechatMiniprogram.InnerAudioContext,
  elapsedBeforeSeconds: number,
  durationSeconds: number,
): void {
  stopBookTTSProgressTimer(host);
  const tick = () => {
    if (token !== host.playToken || host.audioPlayback.currentAudio() !== audio) {
      stopBookTTSProgressTimer(host);
      return;
    }
    updateBookTTSProgress(
      host,
      elapsedBeforeSeconds + playbackTimeForProgress(host, audio),
      host.data.bookTTSDurationSeconds || elapsedBeforeSeconds + durationSeconds,
    );
  };
  tick();
  host.ttsProgressTimer = setInterval(tick, 250);
}

export function stopBookTTSProgressTimer(host: BookTTSHost): void {
  if (host.ttsProgressTimer !== null) {
    clearInterval(host.ttsProgressTimer);
    host.ttsProgressTimer = null;
  }
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
  const currentTime = positiveFiniteSeconds(audio.currentTime);
  if (currentTime) {
    return currentTime;
  }
  if (!host.ttsProgressStartedAtMs) {
    return 0;
  }
  return Math.max(0, (Date.now() - host.ttsProgressStartedAtMs) / 1000);
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
