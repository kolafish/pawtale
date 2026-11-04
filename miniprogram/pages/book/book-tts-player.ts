import type { TTSAlignmentSegment } from "../../domain/memoir-audio";
import type { MemoirBookAudioPlan, MemoirBookAudioSegment } from "../../domain/memoir-book";
import { PollingTimer } from "../../services/polling-timer";
import {
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
  bookTTSPreparingText?: string;
}

export interface BookTTSHost {
  data: BookTTSState;
  setData(patch: Partial<BookTTSState>): void;
  audioPlayback: AudioPlaybackController;
  ttsProgress: VoiceAssistantTTSProgressRuntime;
  playToken: number;
  bookTTSChunk?: (MemoirBookAudioSegment & { text: string; bookCharacters: string[] }) | null;
  bookTTSPollTimer?: PollingTimer;
  bookTTSWake?: () => void;
  onBookAudioPosition?(index: number, seconds: number): void;
  // Unicode code-point offset in the audio source, or null to clear the highlight.
  onBookTTSOffset?(offset: number | null): void;
}

export function applyBookTTSProgressPatch(
  host: BookTTSHost,
  patch: VoiceAssistantTTSProgressPatch,
): void {
  const chunk = host.bookTTSChunk;
  const spoken = chunk || patch.ttsCurrentText
    ? (chunk?.text_start ?? 0) + Array.from(patch.ttsSpokenText).length : 0;
  const progress = chunk
    ? Math.min(100, Math.floor(spoken / Math.max(1, chunk.bookCharacters.length) * 100))
    : patch.ttsProgressPercent;
  if (host.onBookTTSOffset) {
    host.setData({ bookTTSProgressPercent: progress });
    host.onBookTTSOffset(patch.ttsCurrentText ? spoken : null);
    return;
  }
  host.setData({
    bookTTSSpokenText: chunk ? chunk.bookCharacters.slice(0, spoken).join("") : patch.ttsSpokenText,
    bookTTSCurrentText: patch.ttsCurrentText,
    bookTTSRemainingText: chunk
      ? chunk.bookCharacters.slice(spoken + Array.from(patch.ttsCurrentText).length).join("")
      : patch.ttsRemainingText,
    bookTTSProgressPercent: progress,
  });
}

export type AudioPlanHost = Pick<BookTTSHost,
  "audioPlayback" | "playToken" | "bookTTSPollTimer" | "bookTTSWake">;

export async function playAudioPlanSegments(
  host: AudioPlanHost,
  initialPlan: MemoirBookAudioPlan,
  token: number,
  prepare: (index: number, plan: MemoirBookAudioPlan, retryFailed: boolean) => Promise<MemoirBookAudioPlan>,
  onPreparing: (index: number, count: number) => void,
  playSegment: (segment: MemoirBookAudioSegment) => Promise<unknown>,
  startIndex = 0,
): Promise<void> {
  let plan = initialPlan;
  const isCurrent = () => token === host.playToken;
  const acceptPlan = (next: MemoirBookAudioPlan): MemoirBookAudioPlan => {
    if (next.version_id !== initialPlan.version_id
      || next.source_text_hash !== initialPlan.source_text_hash
      || next.profile_key !== initialPlan.profile_key
      || next.segments.length !== initialPlan.segments.length) {
      throw new Error("音频清单已变化，请重新发起朗读");
    }
    return next;
  };
  if (!isCurrent()) return;
  for (let index = Math.max(0, startIndex); index < plan.segments.length && isCurrent(); index += 1) {
    while (isCurrent() && plan.segments[index].status !== "succeeded") {
      const segment = plan.segments[index];
      if (segment.status === "failed") {
        throw new Error(segment.error_message || "本段朗读生成失败，请重试");
      }
      onPreparing(index, plan.segments.length);
      if (segment.status !== "idle") {
        host.bookTTSPollTimer = new PollingTimer(plan.poll_after_ms);
        await new Promise<void>((resolve) => {
          host.bookTTSWake = resolve;
          host.bookTTSPollTimer?.schedule(resolve);
        });
        if (!isCurrent()) return;
        host.bookTTSWake = undefined;
      }
      if (!isCurrent()) return;
      plan = acceptPlan(await prepare(index, plan, false));
    }
    if (!isCurrent()) return;
    const segment = plan.segments[index];
    if (!segment.audio_file_id) throw new Error("本段没有可播放的音频");
    // Prepare and download the next segment while the current one plays.
    const ahead = index + 1 < plan.segments.length
      ? prepare(index + 1, plan, initialPlan.segments[index + 1].status === "failed").then(async (next) => {
          next = acceptPlan(next);
          if (isCurrent() && next.segments[index + 1].audio_file_id) {
            await host.audioPlayback.prefetchFile(next.segments[index + 1].audio_file_id!);
          }
          return { plan: next, error: null };
        }).catch((error: unknown) => ({ plan: null, error }))
      : null;
    await playSegment(segment);
    if (!isCurrent()) return;
    if (ahead) {
      const next = await ahead;
      if (!isCurrent()) return;
      if (next.error) throw next.error;
      if (next.plan) plan = next.plan;
      if (plan.segments[index + 1].status === "pending" || plan.segments[index + 1].status === "running") {
        plan = acceptPlan(await prepare(index + 1, plan, false));
      }
    }
  }
}

export async function playBookAudioPlan(
  host: BookTTSHost,
  initialPlan: MemoirBookAudioPlan,
  token: number,
  activeText: string,
  prepare: (index: number, plan: MemoirBookAudioPlan, retryFailed: boolean) => Promise<MemoirBookAudioPlan>,
  target = "book",
  resume: { index: number; seconds: number } = { index: 0, seconds: 0 },
): Promise<void> {
  if (token !== host.playToken) return;
  const bookCharacters = Array.from(activeText);
  host.setData({
    bookTTSActiveTarget: target, bookTTSActiveText: activeText,
    bookTTSDurationSeconds: 0, bookTTSAlignment: [],
  });
  try {
    await playAudioPlanSegments(host, initialPlan, token, prepare, (index, count) => {
      host.setData({ playingTarget: "", ttsBusyTarget: target,
        bookTTSPreparingText: `正在准备第 ${index + 1} 段，共 ${count} 段…` });
    }, async (segment) => {
      host.bookTTSChunk = { ...segment, bookCharacters, text: bookCharacters.slice(segment.text_start, segment.text_end).join("") };
      host.setData({ ttsBusyTarget: "", playingTarget: target, bookTTSPreparingText: "" });
      await playBookAudioFile(host, segment.audio_file_id!, token, 0, positiveFiniteSeconds(segment.duration_seconds), segment.index === resume.index ? resume.seconds : 0);
    }, resume.index);
    if (token === host.playToken) {
      host.bookTTSChunk = null;
      applyBookTTSProgressPatch(host, {
        ttsSpokenText: activeText, ttsCurrentText: "", ttsRemainingText: "", ttsProgressPercent: 100,
      });
    }
  } catch (error) {
    if (token === host.playToken) resetBookTTSProgress(host);
    throw error;
  } finally {
    if (token === host.playToken) {
      host.bookTTSChunk = null;
      host.setData({ playingTarget: "", ttsBusyTarget: "" });
      stopBookTTSProgressTimer(host);
    }
  }
}

export async function playBookAudioFile(
  host: BookTTSHost,
  audioFileId: string,
  token: number,
  elapsedBeforeSeconds: number,
  fallbackDurationSeconds: number,
  startTime = 0,
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
    startTime,
    onPlay: (audio) => {
      if (token !== host.playToken || host.audioPlayback.currentAudio() !== audio) return;
      host.ttsProgress.markPlaybackStarted();
      startBookTTSProgressTimer(host, token, audio, elapsedBeforeSeconds, fallbackDurationSeconds);
    },
    onTimeUpdate: (audio) => {
      if (token === host.playToken && host.audioPlayback.currentAudio() === audio) {
        if (host.bookTTSChunk) host.onBookAudioPosition?.(host.bookTTSChunk.index, playbackTimeForProgress(host, audio));
        updateBookTTSProgress(
          host,
          elapsedBeforeSeconds + playbackTimeForProgress(host, audio),
          host.data.bookTTSDurationSeconds,
        );
      }
    },
    onEnded: (audio) => {
      if (token !== host.playToken || host.audioPlayback.currentAudio() !== audio) return;
      const seconds = elapsedSeconds(audio);
      playedSeconds = seconds;
      updateBookTTSProgress(host, elapsedBeforeSeconds + seconds, host.data.bookTTSDurationSeconds);
      stopBookTTSProgressTimer(host);
    },
    onStop: (audio) => {
      if (token !== host.playToken || host.audioPlayback.currentAudio() !== audio) return;
      playedSeconds = playbackTimeForProgress(host, audio);
      stopBookTTSProgressTimer(host);
    },
    onError: (_error, audio) => {
      if (token !== host.playToken || host.audioPlayback.currentAudio() !== audio) return;
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
  const chunk = host.bookTTSChunk;
  host.ttsProgress.updateAudioFileProgress(
    chunk?.text ?? activeText,
    currentTimeSeconds,
    chunk ? positiveFiniteSeconds(chunk.duration_seconds) : positiveFiniteSeconds(durationSeconds) || host.data.bookTTSDurationSeconds,
    0,
    chunk ? chunk.alignment : host.data.bookTTSAlignment,
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
    text: () => host.bookTTSChunk?.text ?? host.data.bookTTSActiveText,
    alignment: () => host.bookTTSChunk?.alignment || host.data.bookTTSAlignment,
    fallbackDurationSeconds: host.data.bookTTSDurationSeconds || elapsedBeforeSeconds + durationSeconds,
    applyPatch: (patch) => applyBookTTSProgressPatch(host, patch),
  });
}

export function stopBookTTSProgressTimer(host: BookTTSHost): void {
  host.ttsProgress.stopTimer();
}

export function resetBookTTSProgress(host: BookTTSHost): void {
  host.bookTTSChunk = null;
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
    bookTTSPreparingText: "",
  });
  host.onBookTTSOffset?.(null);
}

export function playbackTimeForProgress(
  host: BookTTSHost,
  audio: WechatMiniprogram.InnerAudioContext,
): number {
  return host.ttsProgress.playbackTimeForAudio(audio);
}

export function stopAudioPlanPolling(host: AudioPlanHost): void {
  host.bookTTSPollTimer?.cancel();
  host.bookTTSWake?.();
  host.bookTTSWake = undefined;
}

export function stopBookPlayback(host: BookTTSHost): void {
  stopAudioPlanPolling(host);
  host.audioPlayback.stop();
  host.playToken = host.audioPlayback.currentToken();
  stopBookTTSProgressTimer(host);
  if (host.data.playingTarget || host.data.ttsBusyTarget) {
    host.setData({ playingTarget: "", ttsBusyTarget: "" });
  }
  resetBookTTSProgress(host);
}
