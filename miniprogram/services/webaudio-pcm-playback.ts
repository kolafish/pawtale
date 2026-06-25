export interface PCMPlaybackFormat {
  sampleRate: number;
  channels: number;
  bitsPerSample: 16;
}

export interface WebAudioPCMPlaybackQueueOptions {
  createContext?: () => WechatMiniprogram.WebAudioContext;
  configureAudio?: () => void;
  minLeadTimeSeconds?: number;
  onSegmentScheduled?: (event: PCMPlaybackSegmentEvent) => void;
  onSegmentEnded?: (event: PCMPlaybackSegmentEvent) => void;
  onUnderrun?: (event: PCMPlaybackUnderrunEvent) => void;
}

export interface PCMPlaybackSegmentEvent {
  segmentIndex: number;
  durationSeconds: number;
  startAtSeconds: number;
  queuedAheadSeconds: number;
  ended: Promise<void>;
}

export interface PCMPlaybackUnderrunEvent {
  segmentIndex: number;
  driftSeconds: number;
  currentTimeSeconds: number;
  nextPlayTimeSeconds: number;
}

export interface WebAudioPCMPlaybackStats {
  segmentCount: number;
  underrunCount: number;
  nextPlayTimeSeconds: number;
  currentTimeSeconds: number;
  queuedAheadSeconds: number;
}

type ScheduledPCMSource = {
  source: WechatMiniprogram.AudioBufferSourceNode;
  event: PCMPlaybackSegmentEvent;
  resolveEnded: () => void;
  ended: boolean;
};

const DEFAULT_MIN_LEAD_SECONDS = 0.12;

export function configureAudibleWebAudioPlayback(): void {
  if (typeof wx !== "undefined" && typeof wx.setInnerAudioOption === "function") {
    wx.setInnerAudioOption({ obeyMuteSwitch: false, mixWithOther: true });
  }
}

export class WebAudioPCMPlaybackQueue {
  private audioContext: WechatMiniprogram.WebAudioContext | null = null;
  private nextPlayTimeSeconds = 0;
  private segmentIndex = 0;
  private underrunCount = 0;
  private stopped = false;
  private sources: ScheduledPCMSource[] = [];

  constructor(private readonly options: WebAudioPCMPlaybackQueueOptions = {}) {}

  async appendPCM16LE(pcm: ArrayBuffer | Uint8Array, format: PCMPlaybackFormat): Promise<PCMPlaybackSegmentEvent | null> {
    const bytes = pcm instanceof Uint8Array ? pcm : new Uint8Array(pcm);
    if (!bytes.byteLength || this.stopped) {
      return null;
    }
    const context = this.context();
    await this.resumeContext(context);
    const buffer = pcm16leToAudioBuffer(context, bytes, format);
    const durationSeconds = buffer.duration;
    if (durationSeconds <= 0) {
      return null;
    }

    const currentTimeSeconds = context.currentTime;
    const minLeadTimeSeconds = Math.max(0, this.options.minLeadTimeSeconds ?? DEFAULT_MIN_LEAD_SECONDS);
    const minimumStartAtSeconds = currentTimeSeconds + minLeadTimeSeconds;
    const segmentIndex = this.segmentIndex;
    let startAtSeconds = this.nextPlayTimeSeconds > 0
      ? this.nextPlayTimeSeconds
      : minimumStartAtSeconds;
    if (startAtSeconds < minimumStartAtSeconds) {
      this.underrunCount += 1;
      this.options.onUnderrun?.({
        segmentIndex,
        driftSeconds: minimumStartAtSeconds - startAtSeconds,
        currentTimeSeconds,
        nextPlayTimeSeconds: this.nextPlayTimeSeconds,
      });
      startAtSeconds = minimumStartAtSeconds;
    }

    let resolveEnded: () => void = () => undefined;
    const ended = new Promise<void>((resolve) => {
      resolveEnded = resolve;
    });
    const source = context.createBufferSource();
    source.buffer = buffer;
    source.connect(context.destination);
    const event: PCMPlaybackSegmentEvent = {
      segmentIndex,
      durationSeconds,
      startAtSeconds,
      queuedAheadSeconds: Math.max(0, startAtSeconds + durationSeconds - currentTimeSeconds),
      ended,
    };
    const scheduledSource: ScheduledPCMSource = {
      source,
      event,
      resolveEnded,
      ended: false,
    };
    source.onended = () => {
      this.finishSource(scheduledSource, true);
    };
    this.sources.push(scheduledSource);
    try {
      source.start(startAtSeconds);
    } catch (error) {
      this.finishSource(scheduledSource, false);
      throw error;
    }
    this.nextPlayTimeSeconds = startAtSeconds + durationSeconds;
    this.segmentIndex += 1;
    this.options.onSegmentScheduled?.(event);
    return event;
  }

  stats(): WebAudioPCMPlaybackStats {
    const currentTimeSeconds = this.audioContext?.currentTime ?? 0;
    return {
      segmentCount: this.segmentIndex,
      underrunCount: this.underrunCount,
      nextPlayTimeSeconds: this.nextPlayTimeSeconds,
      currentTimeSeconds,
      queuedAheadSeconds: Math.max(0, this.nextPlayTimeSeconds - currentTimeSeconds),
    };
  }

  stop(): void {
    this.stopped = true;
    const sources = this.sources.splice(0);
    sources.forEach((scheduledSource) => {
      try {
        scheduledSource.source.stop?.();
      } catch {
        // Some WebAudio implementations throw if a source already ended.
      }
      this.finishSource(scheduledSource, false);
    });
    this.nextPlayTimeSeconds = 0;
  }

  async destroy(): Promise<void> {
    this.stop();
    const context = this.audioContext;
    this.audioContext = null;
    if (context && context.state !== "closed") {
      await context.close();
    }
  }

  private context(): WechatMiniprogram.WebAudioContext {
    if (this.audioContext) {
      return this.audioContext;
    }
    (this.options.configureAudio || configureAudibleWebAudioPlayback)();
    const createContext = this.options.createContext
      || (typeof wx !== "undefined" ? wx.createWebAudioContext?.bind(wx) : undefined);
    if (!createContext) {
      throw new Error("当前微信版本不支持 WebAudio PCM 播放");
    }
    this.stopped = false;
    this.audioContext = createContext();
    return this.audioContext;
  }

  private async resumeContext(context: WechatMiniprogram.WebAudioContext): Promise<void> {
    if (context.state === "suspended") {
      await context.resume();
    }
  }

  private finishSource(scheduledSource: ScheduledPCMSource, notifyEnded: boolean): void {
    if (scheduledSource.ended) {
      return;
    }
    scheduledSource.ended = true;
    const index = this.sources.indexOf(scheduledSource);
    if (index >= 0) {
      this.sources.splice(index, 1);
    }
    scheduledSource.source.disconnect?.();
    if (notifyEnded) {
      this.options.onSegmentEnded?.(scheduledSource.event);
    }
    scheduledSource.resolveEnded();
  }
}

export function pcm16leToAudioBuffer(
  context: WechatMiniprogram.WebAudioContext,
  pcm: Uint8Array,
  format: PCMPlaybackFormat,
): WechatMiniprogram.AudioBuffer {
  if (format.bitsPerSample !== 16) {
    throw new Error("WebAudio PCM 播放目前只支持 16-bit PCM");
  }
  const channels = Math.floor(format.channels);
  if (!Number.isFinite(channels) || channels <= 0) {
    throw new Error("WebAudio PCM 播放缺少有效声道数");
  }
  const sampleRate = Math.floor(format.sampleRate);
  if (!Number.isFinite(sampleRate) || sampleRate < 8000) {
    throw new Error("WebAudio PCM 播放缺少有效采样率");
  }
  const bytesPerFrame = channels * 2;
  const frameCount = Math.floor(pcm.byteLength / bytesPerFrame);
  const buffer = context.createBuffer(channels, frameCount, sampleRate);
  const view = new DataView(pcm.buffer, pcm.byteOffset, pcm.byteLength);
  const channelData = Array.from({ length: channels }, (_unused, channel) => buffer.getChannelData(channel));
  for (let frame = 0; frame < frameCount; frame += 1) {
    const frameOffset = frame * bytesPerFrame;
    for (let channel = 0; channel < channels; channel += 1) {
      const sample = view.getInt16(frameOffset + channel * 2, true);
      channelData[channel][frame] = sample < 0 ? sample / 32768 : sample / 32767;
    }
  }
  return buffer;
}
