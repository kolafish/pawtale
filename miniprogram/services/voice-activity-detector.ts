import { estimatePcm16Level, pcmFrameDurationMs } from "./memoir-streaming-asr-protocol";

export const AUTO_STOP_SILENCE_MS = 3500;
export const AUTO_STOP_MIN_RECORDING_MS = 2000;

export interface VoiceActivityDetectorOptions {
  silenceLevelThreshold: number;
  minVoiceActivityMs: number;
  trackPreVoiceSilence?: boolean;
}

export interface VoiceActivityState {
  audioLevel: number;
  recordingElapsedMs: number;
  silentElapsedMs: number;
  voiceActivityElapsedMs: number;
  hasVoiceActivity: boolean;
}

export class VoiceActivityDetector {
  private audioLevel = 0;
  private recordingElapsedMs = 0;
  private silentElapsedMs = 0;
  private voiceActivityElapsedMs = 0;
  private hasVoiceActivity = false;

  constructor(private readonly options: VoiceActivityDetectorOptions) {}

  reset(): void {
    this.audioLevel = 0;
    this.recordingElapsedMs = 0;
    this.silentElapsedMs = 0;
    this.voiceActivityElapsedMs = 0;
    this.hasVoiceActivity = false;
  }

  update(frameBuffer: ArrayBuffer): VoiceActivityState {
    const frameDurationMs = pcmFrameDurationMs(frameBuffer);
    this.recordingElapsedMs += frameDurationMs;
    this.audioLevel = estimatePcm16Level(frameBuffer);
    if (this.audioLevel >= this.options.silenceLevelThreshold) {
      this.voiceActivityElapsedMs += frameDurationMs;
      this.silentElapsedMs = 0;
      if (this.voiceActivityElapsedMs >= this.options.minVoiceActivityMs) {
        this.hasVoiceActivity = true;
      }
      return this.state();
    }
    this.voiceActivityElapsedMs = 0;
    if (this.hasVoiceActivity || this.options.trackPreVoiceSilence) {
      this.silentElapsedMs += frameDurationMs;
    }
    return this.state();
  }

  state(): VoiceActivityState {
    return {
      audioLevel: this.audioLevel,
      recordingElapsedMs: this.recordingElapsedMs,
      silentElapsedMs: this.silentElapsedMs,
      voiceActivityElapsedMs: this.voiceActivityElapsedMs,
      hasVoiceActivity: this.hasVoiceActivity,
    };
  }
}
