import { estimatePcm16Level, pcmFrameDurationMs } from "./memoir-streaming-asr-protocol";
import { sharedRecorderResource, WechatRecorderResource } from "./recorder-resource";

const DEFAULT_WARMUP_MS = 500;
const DEFAULT_TRIGGER_VOICE_MS = 280;
const DEFAULT_MIN_TRIGGER_LEVEL = 0.025;
const DEFAULT_BASELINE_MULTIPLIER = 1.45;
const DEFAULT_PEAK_MULTIPLIER = 1.12;
const DEFAULT_RECORDING_DURATION_MS = 10 * 60 * 1000;
const DEFAULT_FRAME_SIZE_KB = 4;
const BARGE_IN_RECORDER_OWNER_ID = "voice-barge-in-monitor";

export interface VoiceBargeInMonitorOptions {
  warmupMs?: number;
  triggerVoiceMs?: number;
  minTriggerLevel?: number;
  baselineMultiplier?: number;
  onBargeIn: () => void;
  onError?: (message: string) => void;
}

export interface VoiceBargeInMonitorState {
  audioLevel: number;
  baselineLevel: number;
  triggerLevel: number;
  elapsedMs: number;
  voiceElapsedMs: number;
}

type RecorderManagerFactory = () => WechatMiniprogram.RecorderManager;

export class VoiceBargeInMonitor {
  private running = false;
  private recorderActive = false;
  private triggered = false;
  private elapsedMs = 0;
  private voiceElapsedMs = 0;
  private warmupPeakLevel = 0;
  private warmupLevelSum = 0;
  private warmupFrameCount = 0;
  private lastAudioLevel = 0;
  private options: Required<VoiceBargeInMonitorOptions> | null = null;
  private recorderResource: WechatRecorderResource;

  constructor(
    recorderResourceOrFactory: WechatRecorderResource | RecorderManagerFactory = sharedRecorderResource,
  ) {
    this.recorderResource = recorderResourceOrFactory instanceof WechatRecorderResource
      ? recorderResourceOrFactory
      : new WechatRecorderResource(recorderResourceOrFactory);
  }

  start(options: VoiceBargeInMonitorOptions): boolean {
    if (this.running || this.recorderActive) {
      return false;
    }
    const normalizedOptions: Required<VoiceBargeInMonitorOptions> = {
      warmupMs: options.warmupMs ?? DEFAULT_WARMUP_MS,
      triggerVoiceMs: options.triggerVoiceMs ?? DEFAULT_TRIGGER_VOICE_MS,
      minTriggerLevel: options.minTriggerLevel ?? DEFAULT_MIN_TRIGGER_LEVEL,
      baselineMultiplier: options.baselineMultiplier ?? DEFAULT_BASELINE_MULTIPLIER,
      onBargeIn: options.onBargeIn,
      onError: options.onError ?? (() => undefined),
    };
    this.running = true;
    this.triggered = false;
    this.elapsedMs = 0;
    this.voiceElapsedMs = 0;
    this.warmupPeakLevel = 0;
    this.warmupLevelSum = 0;
    this.warmupFrameCount = 0;
    this.lastAudioLevel = 0;
    this.options = normalizedOptions;
    try {
      const started = this.recorderResource.start(BARGE_IN_RECORDER_OWNER_ID, {
        duration: DEFAULT_RECORDING_DURATION_MS,
        sampleRate: 16000,
        numberOfChannels: 1,
        encodeBitRate: 96000,
        format: "PCM",
        frameSize: DEFAULT_FRAME_SIZE_KB,
      }, {
        onFrame: (result) => {
          this.handleFrame(result.frameBuffer);
        },
        onError: (message) => {
          this.handleRecorderError(message);
        },
        onStop: () => {
          this.markStopped();
        },
      });
      if (!started) {
        this.running = false;
        this.options = null;
        normalizedOptions.onError("麦克风正在使用中");
        return false;
      }
      this.recorderActive = true;
      return true;
    } catch (error) {
      this.running = false;
      const message = error instanceof Error ? error.message : "打断监听启动失败";
      normalizedOptions.onError(message);
      this.options = null;
      return false;
    }
  }

  stop(): void {
    void this.stopAndWait();
  }

  stopAndWait(): Promise<void> {
    if (!this.running && !this.recorderActive) {
      return Promise.resolve();
    }
    this.running = false;
    this.triggered = false;
    this.options = null;
    return this.recorderResource.stopAndWait(BARGE_IN_RECORDER_OWNER_ID);
  }

  isRunning(): boolean {
    return this.running;
  }

  state(): VoiceBargeInMonitorState {
    return {
      audioLevel: this.lastAudioLevel,
      baselineLevel: this.averageWarmupLevel(),
      triggerLevel: this.currentTriggerLevel(),
      elapsedMs: this.elapsedMs,
      voiceElapsedMs: this.voiceElapsedMs,
    };
  }

  private handleFrame(frameBuffer: ArrayBuffer): void {
    if (!this.running || this.triggered || !this.options) {
      return;
    }
    const frameDurationMs = pcmFrameDurationMs(frameBuffer);
    this.elapsedMs += frameDurationMs;
    this.lastAudioLevel = estimatePcm16Level(frameBuffer);
    if (this.elapsedMs <= this.options.warmupMs) {
      this.warmupPeakLevel = Math.max(this.warmupPeakLevel, this.lastAudioLevel);
      this.warmupLevelSum += this.lastAudioLevel;
      this.warmupFrameCount += 1;
      return;
    }
    if (this.lastAudioLevel >= this.currentTriggerLevel()) {
      this.voiceElapsedMs += frameDurationMs;
    } else {
      this.voiceElapsedMs = 0;
    }
    if (this.voiceElapsedMs >= this.options.triggerVoiceMs) {
      this.trigger();
    }
  }

  private currentTriggerLevel(): number {
    const baselineLevel = this.averageWarmupLevel() * (this.options?.baselineMultiplier ?? DEFAULT_BASELINE_MULTIPLIER);
    const peakGuardLevel = this.warmupPeakLevel * DEFAULT_PEAK_MULTIPLIER;
    const minLevel = this.options?.minTriggerLevel ?? DEFAULT_MIN_TRIGGER_LEVEL;
    return Math.max(minLevel, baselineLevel, peakGuardLevel);
  }

  private averageWarmupLevel(): number {
    return this.warmupFrameCount > 0 ? this.warmupLevelSum / this.warmupFrameCount : 0;
  }

  private trigger(): void {
    if (!this.running || this.triggered || !this.options) {
      return;
    }
    const onBargeIn = this.options.onBargeIn;
    this.triggered = true;
    this.running = false;
    onBargeIn();
  }

  private handleRecorderError(message: string): void {
    if (!this.running && !this.recorderActive) {
      return;
    }
    const onError = this.options?.onError;
    this.markStopped();
    onError?.(message);
  }

  private markStopped(): void {
    if (!this.running && !this.recorderActive) {
      return;
    }
    this.running = false;
    this.recorderActive = false;
    this.triggered = false;
    this.options = null;
  }
}
