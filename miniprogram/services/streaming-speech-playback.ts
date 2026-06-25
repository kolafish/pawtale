import {
  type PCMPlaybackFormat,
  type PCMPlaybackSegmentEvent,
  type PCMPlaybackUnderrunEvent,
  type WebAudioPCMPlaybackStats,
  WebAudioPCMPlaybackQueue,
} from "./webaudio-pcm-playback";

export interface StreamingSpeechPCMFrame {
  audio: ArrayBuffer | Uint8Array;
  mimeType?: string;
  sampleRate: number;
  channels: number;
  bitsPerSample: 16;
}

export interface StreamingSpeechPlaybackFrameEvent extends PCMPlaybackSegmentEvent {
  frameIndex: number;
}

export interface StreamingSpeechPlaybackControllerOptions {
  createContext?: () => WechatMiniprogram.WebAudioContext;
  minLeadTimeSeconds?: number;
  onFrameScheduled?: (event: StreamingSpeechPlaybackFrameEvent) => void;
  onFrameEnded?: (event: StreamingSpeechPlaybackFrameEvent) => void;
  onUnderrun?: (event: PCMPlaybackUnderrunEvent) => void;
}

export class StreamingSpeechPlaybackController {
  private queue: WebAudioPCMPlaybackQueue | null = null;
  private pendingEnds: Promise<void>[] = [];
  private stopped = false;
  private frameIndex = 0;

  constructor(private readonly options: StreamingSpeechPlaybackControllerOptions = {}) {}

  async appendFrame(frame: StreamingSpeechPCMFrame): Promise<StreamingSpeechPlaybackFrameEvent | null> {
    if (this.stopped) {
      return null;
    }
    const format = this.validateFrame(frame);
    const scheduled = await this.playbackQueue().appendPCM16LE(frame.audio, format);
    if (!scheduled) {
      return null;
    }
    const event: StreamingSpeechPlaybackFrameEvent = {
      ...scheduled,
      frameIndex: this.frameIndex,
    };
    this.frameIndex += 1;
    this.options.onFrameScheduled?.(event);
    const ended = scheduled.ended.then(() => {
      if (!this.stopped) {
        this.options.onFrameEnded?.(event);
      }
    });
    this.pendingEnds.push(ended);
    return event;
  }

  async waitForDrain(): Promise<void> {
    await Promise.all(this.pendingEnds);
  }

  stats(): WebAudioPCMPlaybackStats {
    return this.queue?.stats() ?? {
      segmentCount: 0,
      underrunCount: 0,
      nextPlayTimeSeconds: 0,
      currentTimeSeconds: 0,
      queuedAheadSeconds: 0,
    };
  }

  stop(): void {
    this.stopped = true;
    this.queue?.stop();
  }

  async destroy(): Promise<void> {
    this.stop();
    await this.queue?.destroy();
    this.queue = null;
    this.pendingEnds = [];
  }

  private playbackQueue(): WebAudioPCMPlaybackQueue {
    if (!this.queue) {
      this.stopped = false;
      this.queue = new WebAudioPCMPlaybackQueue({
        createContext: this.options.createContext,
        minLeadTimeSeconds: this.options.minLeadTimeSeconds,
        onUnderrun: this.options.onUnderrun,
      });
    }
    return this.queue;
  }

  private validateFrame(frame: StreamingSpeechPCMFrame): PCMPlaybackFormat {
    const mimeType = (frame.mimeType || "audio/pcm").split(";")[0].trim().toLowerCase();
    if (mimeType !== "audio/l16" && mimeType !== "audio/pcm" && mimeType !== "audio/x-pcm") {
      throw new Error("流式语音播放只支持 PCM 音频帧");
    }
    if (frame.bitsPerSample !== 16) {
      throw new Error("流式语音播放只支持 16-bit PCM");
    }
    if (!Number.isFinite(frame.sampleRate) || frame.sampleRate <= 0) {
      throw new Error("流式语音播放缺少有效采样率");
    }
    if (!Number.isFinite(frame.channels) || frame.channels <= 0) {
      throw new Error("流式语音播放缺少有效声道数");
    }
    return {
      sampleRate: Math.floor(frame.sampleRate),
      channels: Math.floor(frame.channels),
      bitsPerSample: 16,
    };
  }
}
