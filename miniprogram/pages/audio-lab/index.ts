import {
  WebAudioPCMPlaybackQueue,
  sinePCM16LEChunk,
  type PCMPlaybackSegmentEvent,
  type PCMPlaybackUnderrunEvent,
} from "../../services/webaudio-pcm-playback";

interface AudioLabData {
  supported: boolean;
  running: boolean;
  statusText: string;
  scheduledCount: number;
  endedCount: number;
  underrunCount: number;
  queuedAheadText: string;
  lastEventText: string;
}

const SAMPLE_RATE = 16000;
const CHUNK_SECONDS = 0.1;
const TOTAL_SECONDS = 12;
const EMIT_INTERVAL_MS = 80;

Page<AudioLabData, {
  playbackQueue: WebAudioPCMPlaybackQueue | null;
  emitTimer: number | null;
  phase: number;
  emittedChunks: number;
  endedCount: number;
  onLoad(): void;
  startWebAudioPCMTest(): Promise<void>;
  stopWebAudioPCMTest(): void;
  scheduleNextChunk(): void;
  appendTestChunk(): Promise<void>;
  updateStats(lastEventText?: string): void;
  handleSegmentScheduled(event: PCMPlaybackSegmentEvent): void;
  handleSegmentEnded(event: PCMPlaybackSegmentEvent): void;
  handleUnderrun(event: PCMPlaybackUnderrunEvent): void;
}>({
  data: {
    supported: false,
    running: false,
    statusText: "未开始",
    scheduledCount: 0,
    endedCount: 0,
    underrunCount: 0,
    queuedAheadText: "0.00s",
    lastEventText: "",
  },

  playbackQueue: null,
  emitTimer: null,
  phase: 0,
  emittedChunks: 0,
  endedCount: 0,

  onLoad() {
    this.setData({
      supported: typeof wx.createWebAudioContext === "function",
      statusText: typeof wx.createWebAudioContext === "function"
        ? "可开始 WebAudio PCM 验证"
        : "当前微信版本不支持 WebAudio",
    });
  },

  async startWebAudioPCMTest() {
    this.stopWebAudioPCMTest();
    if (typeof wx.createWebAudioContext !== "function") {
      this.setData({ statusText: "当前微信版本不支持 WebAudio" });
      return;
    }
    this.phase = 0;
    this.emittedChunks = 0;
    this.endedCount = 0;
    this.playbackQueue = new WebAudioPCMPlaybackQueue({
      minLeadTimeSeconds: 0.25,
      onSegmentScheduled: (event) => this.handleSegmentScheduled(event),
      onSegmentEnded: (event) => this.handleSegmentEnded(event),
      onUnderrun: (event) => this.handleUnderrun(event),
    });
    this.setData({
      running: true,
      statusText: "正在推送 100ms PCM chunk",
      scheduledCount: 0,
      endedCount: 0,
      underrunCount: 0,
      queuedAheadText: "0.00s",
      lastEventText: "",
    });
    await this.appendTestChunk();
    this.scheduleNextChunk();
  },

  stopWebAudioPCMTest() {
    if (this.emitTimer !== null) {
      clearTimeout(this.emitTimer);
      this.emitTimer = null;
    }
    void this.playbackQueue?.destroy();
    this.playbackQueue = null;
    this.setData({
      running: false,
      statusText: "已停止",
      queuedAheadText: "0.00s",
    });
  },

  scheduleNextChunk() {
    if (!this.playbackQueue || !this.data.running) {
      return;
    }
    if (this.emittedChunks >= Math.round(TOTAL_SECONDS / CHUNK_SECONDS)) {
      this.setData({ statusText: "已完成推送，等待播放结束" });
      return;
    }
    this.emitTimer = setTimeout(() => {
      this.emitTimer = null;
      void this.appendTestChunk().then(() => this.scheduleNextChunk());
    }, EMIT_INTERVAL_MS) as unknown as number;
  },

  async appendTestChunk() {
    const queue = this.playbackQueue;
    if (!queue) {
      return;
    }
    const chunk = sinePCM16LEChunk({
      frequencyHz: 440,
      durationSeconds: CHUNK_SECONDS,
      sampleRate: SAMPLE_RATE,
      channels: 1,
      phase: this.phase,
    });
    this.phase = chunk.nextPhase;
    this.emittedChunks += 1;
    await queue.appendPCM16LE(chunk.pcm, {
      sampleRate: SAMPLE_RATE,
      channels: 1,
      bitsPerSample: 16,
    });
    this.updateStats();
  },

  updateStats(lastEventText) {
    const stats = this.playbackQueue?.stats();
    this.setData({
      scheduledCount: stats?.segmentCount || this.data.scheduledCount,
      underrunCount: stats?.underrunCount || this.data.underrunCount,
      endedCount: this.endedCount,
      queuedAheadText: `${(stats?.queuedAheadSeconds || 0).toFixed(2)}s`,
      lastEventText: lastEventText || this.data.lastEventText,
    });
  },

  handleSegmentScheduled(event) {
    this.updateStats(`scheduled #${event.segmentIndex} at ${event.startAtSeconds.toFixed(2)}s`);
  },

  handleSegmentEnded(event) {
    this.endedCount += 1;
    this.updateStats(`ended #${event.segmentIndex}`);
    if (this.emittedChunks >= Math.round(TOTAL_SECONDS / CHUNK_SECONDS) && this.endedCount >= this.emittedChunks) {
      this.setData({ running: false, statusText: "播放完成" });
    }
  },

  handleUnderrun(event) {
    this.updateStats(`underrun #${event.segmentIndex}, drift ${event.driftSeconds.toFixed(3)}s`);
  },
});
