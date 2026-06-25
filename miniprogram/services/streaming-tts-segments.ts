const PCM_SAMPLE_RATE = 16000;
const PCM_CHANNELS = 1;
const PCM_BITS_PER_SAMPLE = 16;

export interface StreamingTTSTextSegmentEvent {
  segment_index: number;
  text: string;
}

export interface StreamingTTSAudioChunkEvent {
  segment_index: number;
  audio: string;
  mime_type: string;
  sample_rate?: number | null;
  channels?: number | null;
  bits_per_sample?: number | null;
}

export interface StreamingTTSAudioSegmentDoneEvent {
  segment_index: number;
  duration_seconds?: number | null;
}

export interface PreparedStreamingTTSAudioSegment {
  segmentIndex: number;
  text: string;
  tempFilePath: string;
  mimeType: string;
  durationSeconds?: number | null;
  dispose(): void;
}

type SegmentBuffer = {
  text: string;
  mimeType: string;
  sampleRate: number;
  channels: number;
  bitsPerSample: number;
  chunks: Uint8Array[];
};

export class StreamingTTSSegmentAssembler {
  private readonly segments = new Map<number, SegmentBuffer>();
  private readonly tempFiles: string[] = [];

  constructor(private readonly filePrefix: string) {}

  rememberText(event: StreamingTTSTextSegmentEvent): void {
    const segment = this.segmentBuffer(event.segment_index);
    segment.text = event.text || segment.text;
  }

  appendChunk(event: StreamingTTSAudioChunkEvent): void {
    const audio = base64ToUint8Array(event.audio);
    if (!audio.byteLength) {
      return;
    }
    const segment = this.segmentBuffer(event.segment_index);
    segment.mimeType = event.mime_type || segment.mimeType;
    segment.sampleRate = positiveInteger(event.sample_rate) || segment.sampleRate;
    segment.channels = positiveInteger(event.channels) || segment.channels;
    segment.bitsPerSample = positiveInteger(event.bits_per_sample) || segment.bitsPerSample;
    segment.chunks.push(audio);
  }

  async finalize(event: StreamingTTSAudioSegmentDoneEvent): Promise<PreparedStreamingTTSAudioSegment | null> {
    const segment = this.segments.get(event.segment_index);
    if (!segment || !segment.chunks.length) {
      this.segments.delete(event.segment_index);
      return null;
    }
    this.segments.delete(event.segment_index);
    const audio = concatUint8Arrays(segment.chunks);
    const stored = audioBytesForPlayback(audio, segment.mimeType, {
      sampleRate: segment.sampleRate,
      channels: segment.channels,
      bitsPerSample: segment.bitsPerSample,
    });
    const filePath = `${wx.env.USER_DATA_PATH}/${safeFilePart(this.filePrefix)}-${event.segment_index}${stored.extension}`;
    await writeTempFile(filePath, stored.bytes);
    this.tempFiles.push(filePath);
    let disposed = false;
    return {
      segmentIndex: event.segment_index,
      text: segment.text,
      tempFilePath: filePath,
      mimeType: stored.mimeType,
      durationSeconds: event.duration_seconds,
      dispose: () => {
        if (disposed) {
          return;
        }
        disposed = true;
        this.removeTempFile(filePath);
        this.deleteTempFile(filePath);
      },
    };
  }

  disposeTempFiles(): void {
    this.tempFiles.splice(0).forEach((filePath) => this.deleteTempFile(filePath));
  }

  private removeTempFile(filePath: string): void {
    const index = this.tempFiles.indexOf(filePath);
    if (index >= 0) {
      this.tempFiles.splice(index, 1);
    }
  }

  private deleteTempFile(filePath: string): void {
    wx.getFileSystemManager().unlink?.({ filePath });
  }

  private segmentBuffer(segmentIndex: number): SegmentBuffer {
    const current = this.segments.get(segmentIndex);
    if (current) {
      return current;
    }
    const created: SegmentBuffer = {
      text: "",
      mimeType: "audio/pcm",
      sampleRate: PCM_SAMPLE_RATE,
      channels: PCM_CHANNELS,
      bitsPerSample: PCM_BITS_PER_SAMPLE,
      chunks: [],
    };
    this.segments.set(segmentIndex, created);
    return created;
  }
}

function base64ToUint8Array(value: string): Uint8Array {
  if (!value) {
    return new Uint8Array();
  }
  const buffer = wx.base64ToArrayBuffer(value);
  return new Uint8Array(buffer);
}

function concatUint8Arrays(chunks: Uint8Array[]): Uint8Array {
  const totalLength = chunks.reduce((total, chunk) => total + chunk.byteLength, 0);
  const output = new Uint8Array(totalLength);
  let offset = 0;
  chunks.forEach((chunk) => {
    output.set(chunk, offset);
    offset += chunk.byteLength;
  });
  return output;
}

function audioBytesForPlayback(audio: Uint8Array, mimeType: string, options: {
  sampleRate: number;
  channels: number;
  bitsPerSample: number;
}): {
  bytes: Uint8Array;
  extension: string;
  mimeType: string;
} {
  const normalized = (mimeType || "audio/pcm").split(";")[0].trim().toLowerCase();
  if (normalized === "audio/l16" || normalized === "audio/pcm" || normalized === "audio/x-pcm") {
    return {
      bytes: wavBytesFromPcm(audio, options),
      extension: ".wav",
      mimeType: "audio/wav",
    };
  }
  if (normalized === "audio/mpeg" || normalized === "audio/mp3") {
    return { bytes: audio, extension: ".mp3", mimeType: "audio/mpeg" };
  }
  if (normalized === "audio/ogg") {
    return { bytes: audio, extension: ".ogg", mimeType: "audio/ogg" };
  }
  if (normalized === "audio/wav" || normalized === "audio/x-wav" || normalized === "audio/wave") {
    return { bytes: audio, extension: ".wav", mimeType: "audio/wav" };
  }
  return {
    bytes: wavBytesFromPcm(audio, options),
    extension: ".wav",
    mimeType: "audio/wav",
  };
}

function wavBytesFromPcm(pcm: Uint8Array, options: {
  sampleRate: number;
  channels: number;
  bitsPerSample: number;
}): Uint8Array {
  const sampleRate = options.sampleRate || PCM_SAMPLE_RATE;
  const channels = options.channels || PCM_CHANNELS;
  const bitsPerSample = options.bitsPerSample || PCM_BITS_PER_SAMPLE;
  const header = new ArrayBuffer(44);
  const view = new DataView(header);
  writeAscii(view, 0, "RIFF");
  view.setUint32(4, 36 + pcm.byteLength, true);
  writeAscii(view, 8, "WAVE");
  writeAscii(view, 12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, channels, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * channels * bitsPerSample / 8, true);
  view.setUint16(32, channels * bitsPerSample / 8, true);
  view.setUint16(34, bitsPerSample, true);
  writeAscii(view, 36, "data");
  view.setUint32(40, pcm.byteLength, true);
  const output = new Uint8Array(44 + pcm.byteLength);
  output.set(new Uint8Array(header), 0);
  output.set(pcm, 44);
  return output;
}

function writeAscii(view: DataView, offset: number, text: string): void {
  for (let index = 0; index < text.length; index += 1) {
    view.setUint8(offset + index, text.charCodeAt(index));
  }
}

function writeTempFile(filePath: string, data: Uint8Array): Promise<void> {
  return new Promise((resolve, reject) => {
    wx.getFileSystemManager().writeFile({
      filePath,
      data: uint8ArrayToArrayBuffer(data),
      success: () => resolve(),
      fail: (error) => reject(new Error(error.errMsg || "AI 语音片段写入失败")),
    });
  });
}

function uint8ArrayToArrayBuffer(data: Uint8Array): ArrayBuffer {
  const output = new ArrayBuffer(data.byteLength);
  new Uint8Array(output).set(data);
  return output;
}

function safeFilePart(value: string): string {
  return value.replace(/[^a-zA-Z0-9_-]/g, "");
}

function positiveInteger(value: unknown): number | null {
  const numeric = Number(value);
  return Number.isInteger(numeric) && numeric > 0 ? numeric : null;
}
