import { estimatePcm16Level, pcmFrameDurationMs } from "./memoir-streaming-asr-protocol";

const DEFAULT_WARMUP_MS = 700;
const DEFAULT_TRIGGER_VOICE_MS = 420;
const DEFAULT_MIN_TRIGGER_LEVEL = 0.055;
const DEFAULT_BASELINE_MULTIPLIER = 1.7;
const DEFAULT_RECORDING_DURATION_MS = 10 * 60 * 1000;
const DEFAULT_FRAME_SIZE_KB = 4;
const STOP_SETTLE_TIMEOUT_MS = 1500;

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
  private recorderActive = false;
  private triggered = false;
  private elapsedMs = 0;
  private voiceElapsedMs = 0;
  private warmupPeakLevel = 0;
  private lastAudioLevel = 0;
  private options: Required<VoiceBargeInMonitorOptions> | null = null;
  private stopPromise: Promise<void> | null = null;
  private stopWaiters: Array<() => void> = [];

  constructor(
    private readonly recorderManagerFactory: RecorderManagerFactory = () => wx.getRecorderManager(),
  ) {}

  start(options: VoiceBargeInMonitorOptions): boolean {
    if (this.running || this.recorderActive || this.stopPromise) {
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
    if (this.stopPromise) {
      return this.stopPromise;
    }
    this.running = false;
    this.triggered = false;
    this.options = null;
    this.stopPromise = (async () => {
      const stopped = this.waitForStop();
      try {
        this.recorderManager?.stop();
      } catch {
        this.markStopped();
      }
      await stopped;
    })().finally(() => {
      this.stopPromise = null;
    });
    return this.stopPromise;
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
      this.markStopped();
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

  private waitForStop(): Promise<void> {
    if (!this.recorderActive) {
      return Promise.resolve();
    }
    return new Promise((resolve) => {
      const timer = setTimeout(() => {
        this.markStopped();
      }, STOP_SETTLE_TIMEOUT_MS);
      this.stopWaiters.push(() => {
        clearTimeout(timer);
        resolve();
      });
    });
  }

  private markStopped(): void {
    if (!this.running && !this.recorderActive && !this.stopWaiters.length) {
      return;
    }
    this.running = false;
    this.recorderActive = false;
    this.triggered = false;
    this.options = null;
    const waiters = this.stopWaiters;
    this.stopWaiters = [];
    waiters.forEach((resolve) => resolve());
  }
}
