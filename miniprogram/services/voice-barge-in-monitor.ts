import { estimatePcm16Level, pcmFrameDurationMs } from "./memoir-streaming-asr-protocol";

const DEFAULT_WARMUP_MS = 700;
const DEFAULT_TRIGGER_VOICE_MS = 420;
const DEFAULT_MIN_TRIGGER_LEVEL = 0.055;
const DEFAULT_BASELINE_MULTIPLIER = 1.7;
const DEFAULT_RECORDING_DURATION_MS = 10 * 60 * 1000;
const DEFAULT_FRAME_SIZE_KB = 4;

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
  private recorderManager: WechatMiniprogram.RecorderManager | null = null;
  private recorderEventsBound = false;
  private running = false;
  private triggered = false;
  private elapsedMs = 0;
  private voiceElapsedMs = 0;
  private warmupPeakLevel = 0;
  private lastAudioLevel = 0;
  private options: Required<VoiceBargeInMonitorOptions> | null = null;

  constructor(
    private readonly recorderManagerFactory: RecorderManagerFactory = () => wx.getRecorderManager(),
  ) {}

  start(options: VoiceBargeInMonitorOptions): boolean {
    if (this.running) {
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
    this.lastAudioLevel = 0;
    this.options = normalizedOptions;
    try {
      this.ensureRecorderManager();
      this.recorderManager?.start({
        duration: DEFAULT_RECORDING_DURATION_MS,
        sampleRate: 16000,
        numberOfChannels: 1,
        encodeBitRate: 96000,
        format: "PCM",
        frameSize: DEFAULT_FRAME_SIZE_KB,
      });
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
    if (!this.running) {
      return;
    }
    this.running = false;
    this.triggered = false;
    this.options = null;
    try {
      this.recorderManager?.stop();
    } catch {
      // The recorder may already be stopped by WeChat when playback or page state changes.
    }
  }

  isRunning(): boolean {
    return this.running;
  }

  state(): VoiceBargeInMonitorState {
    return {
      audioLevel: this.lastAudioLevel,
      baselineLevel: this.warmupPeakLevel,
      triggerLevel: this.currentTriggerLevel(),
      elapsedMs: this.elapsedMs,
      voiceElapsedMs: this.voiceElapsedMs,
    };
  }

  private ensureRecorderManager(): void {
    if (!this.recorderManager) {
      this.recorderManager = this.recorderManagerFactory();
    }
    if (this.recorderEventsBound) {
      return;
    }
    this.recorderManager.onFrameRecorded((result) => {
      this.handleFrame(result.frameBuffer);
    });
    this.recorderManager.onError((error) => {
      this.handleRecorderError(error.errMsg || "打断监听失败");
    });
    this.recorderManager.onStop(() => {
      if (this.running) {
        this.running = false;
        this.options = null;
      }
    });
    this.recorderEventsBound = true;
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
    const baselineLevel = this.warmupPeakLevel * (this.options?.baselineMultiplier ?? DEFAULT_BASELINE_MULTIPLIER);
    const minLevel = this.options?.minTriggerLevel ?? DEFAULT_MIN_TRIGGER_LEVEL;
    return Math.max(minLevel, baselineLevel);
  }

  private trigger(): void {
    if (!this.running || this.triggered || !this.options) {
      return;
    }
    const onBargeIn = this.options.onBargeIn;
    this.triggered = true;
    this.stop();
    onBargeIn();
  }

  private handleRecorderError(message: string): void {
    if (!this.running || !this.options) {
      return;
    }
    const onError = this.options.onError;
    this.running = false;
    this.triggered = false;
    this.options = null;
    onError(message);
  }
}
