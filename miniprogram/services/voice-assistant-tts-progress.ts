import type { StreamingSpeechPlaybackFrameEvent } from "./streaming-speech-playback";
import {
  splitTextByAudioPlaybackProgress,
  type TTSAlignmentSegment,
} from "./tts-text-progress";
import { countTextChars } from "./voice-chat-state";

export interface VoiceAssistantTTSProgressPatch {
  ttsSpokenText: string;
  ttsCurrentText: string;
  ttsRemainingText: string;
  ttsProgressPercent: number;
}

type ApplyProgressPatch = (patch: VoiceAssistantTTSProgressPatch) => void;

interface StreamingProgressTimerOptions {
  isActive(): boolean;
  currentTimeSeconds(): number | null | undefined;
  text(): string;
  holdSeconds?: number;
  applyPatch: ApplyProgressPatch;
}

interface AudioFileProgressTimerOptions {
  audio: WechatMiniprogram.InnerAudioContext;
  isActive(): boolean;
  currentTimeOffsetSeconds?: number;
  durationSeconds?(): number | null | undefined;
  text(): string;
  alignment(): TTSAlignmentSegment[];
  fallbackDurationSeconds?: number;
  applyPatch: ApplyProgressPatch;
}

const STREAMING_TTS_PROGRESS_HOLD_SECONDS = 0.2;
const PROGRESS_TIMER_INTERVAL_MS = 250;

export function streamingProgressPercent(
  spokenText: string,
  fullText: string,
  allowComplete: boolean,
): number {
  const totalChars = countTextChars(fullText);
  if (totalChars <= 0) {
    return 0;
  }
  const spokenChars = Math.min(totalChars, countTextChars(spokenText));
  const percent = Math.max(0, Math.round((spokenChars / totalChars) * 100));
  return allowComplete ? percent : Math.min(percent, 99);
}

export class VoiceAssistantTTSProgressRuntime {
  private timer: number | null = null;
  private playbackStartedAtMs = 0;
  private streamingAudioStartSeconds = -1;
  private streamingAudioEndSeconds = 0;
  private streamingAudioDurationSeconds = 0;

  markStreamingAudioFrameScheduled(event: StreamingSpeechPlaybackFrameEvent): void {
    if (this.streamingAudioStartSeconds < 0) {
      this.streamingAudioStartSeconds = event.startAtSeconds;
    }
    const frameEndSeconds = event.startAtSeconds + event.durationSeconds;
    this.streamingAudioEndSeconds = Math.max(this.streamingAudioEndSeconds, frameEndSeconds);
    this.streamingAudioDurationSeconds = Math.max(
      this.streamingAudioDurationSeconds,
      this.streamingAudioEndSeconds - this.streamingAudioStartSeconds,
    );
  }

  hasStreamingAudioFrame(): boolean {
    return this.streamingAudioStartSeconds >= 0;
  }

  streamingDurationSeconds(): number {
    return this.streamingAudioDurationSeconds;
  }

  setStreamingDurationSeconds(durationSeconds: number): void {
    if (Number.isFinite(durationSeconds) && durationSeconds > 0) {
      this.streamingAudioDurationSeconds = durationSeconds;
    }
  }

  resetStreamingAudio(): void {
    this.streamingAudioStartSeconds = -1;
    this.streamingAudioEndSeconds = 0;
    this.streamingAudioDurationSeconds = 0;
  }

  markPlaybackStarted(): void {
    this.playbackStartedAtMs = Date.now();
  }

  playbackTimeForAudio(audio: WechatMiniprogram.InnerAudioContext): number {
    const currentTime = Number(audio.currentTime);
    if (Number.isFinite(currentTime) && currentTime > 0) {
      return currentTime;
    }
    if (this.playbackStartedAtMs > 0) {
      return Math.max(0, (Date.now() - this.playbackStartedAtMs) / 1000);
    }
    return 0;
  }

  updateStreamingProgress(
    text: string,
    currentTimeSeconds: number | null | undefined,
    applyPatch: ApplyProgressPatch,
    holdSeconds: number = STREAMING_TTS_PROGRESS_HOLD_SECONDS,
  ): void {
    if (!text) {
      this.resetProgress(applyPatch);
      return;
    }
    const currentTime = this.streamingPlaybackTimeForProgress(currentTimeSeconds);
    const scheduledDuration = this.streamingAudioStartSeconds < 0
      ? 0
      : Math.max(0, this.streamingAudioEndSeconds - this.streamingAudioStartSeconds);
    const duration = Math.max(
      scheduledDuration,
      currentTime + holdSeconds,
    );
    const progress = splitTextByAudioPlaybackProgress(text, currentTime, duration, {
      alignment: [],
    });
    applyPatch({
      ttsSpokenText: progress.spokenText,
      ttsCurrentText: progress.currentText,
      ttsRemainingText: progress.remainingText,
      ttsProgressPercent: Math.min(progress.progressPercent, 99),
    });
  }

  updateAudioFileProgress(
    text: string,
    currentTime: number,
    duration: number,
    fallbackDuration: number,
    alignment: TTSAlignmentSegment[],
    applyPatch: ApplyProgressPatch,
  ): void {
    if (!text) {
      this.resetProgress(applyPatch);
      return;
    }
    const effectiveDuration = Number.isFinite(duration) && duration > 0 ? duration : fallbackDuration;
    const progress = splitTextByAudioPlaybackProgress(text, currentTime, effectiveDuration, {
      alignment,
    });
    applyPatch({
      ttsSpokenText: progress.spokenText,
      ttsCurrentText: progress.currentText,
      ttsRemainingText: progress.remainingText,
      ttsProgressPercent: progress.progressPercent,
    });
  }

  startStreamingTimer(options: StreamingProgressTimerOptions): void {
    if (this.timer !== null) {
      return;
    }
    this.stopTimer();
    const timer = setInterval(() => {
      if (!options.isActive()) {
        if (this.timer === timer) {
          this.stopTimer();
        }
        return;
      }
      this.updateStreamingProgress(
        options.text(),
        options.currentTimeSeconds(),
        options.applyPatch,
        options.holdSeconds,
      );
    }, PROGRESS_TIMER_INTERVAL_MS);
    this.timer = timer;
  }

  startAudioFileTimer(options: AudioFileProgressTimerOptions): void {
    this.stopTimer();
    if (this.playbackStartedAtMs <= 0) {
      this.markPlaybackStarted();
    }
    const timer = setInterval(() => {
      if (!options.isActive()) {
        if (this.timer === timer) {
          this.stopTimer();
        }
        return;
      }
      const currentTimeOffset = options.currentTimeOffsetSeconds || 0;
      const durationSeconds = options.durationSeconds
        ? Number(options.durationSeconds())
        : options.audio.duration;
      this.updateAudioFileProgress(
        options.text(),
        currentTimeOffset + this.playbackTimeForAudio(options.audio),
        durationSeconds,
        options.fallbackDurationSeconds || 0,
        options.alignment(),
        options.applyPatch,
      );
    }, PROGRESS_TIMER_INTERVAL_MS);
    this.timer = timer;
  }

  stopTimer(): void {
    if (this.timer !== null) {
      clearInterval(this.timer);
      this.timer = null;
    }
    this.playbackStartedAtMs = 0;
  }

  resetProgress(applyPatch: ApplyProgressPatch): void {
    applyPatch({
      ttsSpokenText: "",
      ttsCurrentText: "",
      ttsRemainingText: "",
      ttsProgressPercent: 0,
    });
  }

  private streamingPlaybackTimeForProgress(currentTimeSeconds: number | null | undefined): number {
    if (this.streamingAudioStartSeconds < 0) {
      return 0;
    }
    const current = Number.isFinite(currentTimeSeconds) ? Number(currentTimeSeconds) : 0;
    return Math.max(0, current - this.streamingAudioStartSeconds);
  }
}
