import { backendSessionCookieHeader, bookVoiceAssistantTTSStreamUrl } from "./memoir-voice-api";
import type { StreamingSpeechPCMFrame } from "./streaming-speech-playback";

const SOCKET_CONNECT_TIMEOUT_MS = 10000;

export interface BookVoiceAssistantTTSStartOptions {
  text: string;
  voice?: string;
  speedRatio?: number;
  connectTimeoutMs?: number;
}

export interface BookVoiceAssistantTTSStartedEvent {
  type: "tts.started";
  stream_id: string;
  project_id: string;
  state: string;
  tts_provider?: string;
  tts_model?: string;
  audio_protocol?: string;
}

export interface BookVoiceAssistantTTSTerminalEvent {
  type: "tts.completed" | "tts.failed" | "tts.cancelled";
  stream_id?: string;
  project_id?: string;
  state?: string;
  error?: string;
}

interface BookVoiceAssistantTTSAudioMetadata {
  stream_id: string;
  project_id: string;
  sequence: number;
  mime_type: string;
  provider?: string;
  model?: string;
  voice?: string;
  speed_ratio?: number;
  sample_rate?: number | null;
  channels?: number | null;
  bits_per_sample?: number | null;
}

export interface BookVoiceAssistantTTSAudioFormatEvent extends BookVoiceAssistantTTSAudioMetadata {
  type: "audio.format";
}

export interface BookVoiceAssistantTTSAudioFrameEvent extends BookVoiceAssistantTTSAudioMetadata {
  type: "audio.frame";
  audio: string;
}

export interface BookVoiceAssistantTTSAudioDoneEvent {
  type: "audio.done";
  stream_id: string;
  project_id: string;
  frame_count: number;
  duration_seconds?: number | null;
  provider?: string;
  model?: string;
  voice?: string;
  speed_ratio?: number;
  sample_rate?: number | null;
  channels?: number | null;
  bits_per_sample?: number | null;
}

export interface PreparedBookVoiceAssistantTTSFrame extends StreamingSpeechPCMFrame {
  streamId: string;
  sequence: number;
}

export interface BookVoiceAssistantTTSCallbacks {
  onStarted?(event: BookVoiceAssistantTTSStartedEvent): void;
  onAudioFormat?(event: BookVoiceAssistantTTSAudioFormatEvent): void;
  onAudioFrame?(frame: PreparedBookVoiceAssistantTTSFrame): void;
  onAudioDone?(event: BookVoiceAssistantTTSAudioDoneEvent): void;
  onCompleted?(event: BookVoiceAssistantTTSTerminalEvent): void;
  onCancelled?(event: BookVoiceAssistantTTSTerminalEvent): void;
  onFailed?(event: BookVoiceAssistantTTSTerminalEvent): void;
}

export class BookVoiceAssistantTTSStreamClient {
  private socketTask: WechatMiniprogram.SocketTask | null = null;
  private socketOpen = false;
  private closed = false;
  private startSent = false;
  private messageChain: Promise<void> = Promise.resolve();
  private resolveStart?: (event: BookVoiceAssistantTTSTerminalEvent) => void;
  private rejectStart?: (error: Error) => void;

  constructor(
    private readonly projectId: string,
    private readonly callbacks: BookVoiceAssistantTTSCallbacks = {},
  ) {}

  async start(options: BookVoiceAssistantTTSStartOptions): Promise<BookVoiceAssistantTTSTerminalEvent> {
    this.closed = false;
    this.startSent = false;
    this.messageChain = Promise.resolve();
    const connectTimeoutMs = options.connectTimeoutMs ?? SOCKET_CONNECT_TIMEOUT_MS;
    const cookie = await backendSessionCookieHeader();
    const url = bookVoiceAssistantTTSStreamUrl(this.projectId);
    const startPayload = this.startPayload(options);
    const terminalEvent = await new Promise<BookVoiceAssistantTTSTerminalEvent>((resolve, reject) => {
      this.resolveStart = resolve;
      this.rejectStart = reject;
      let connectTimer: ReturnType<typeof setTimeout> | null = setTimeout(() => {
        connectTimer = null;
        if (this.closed || this.socketOpen) {
          return;
        }
        this.closed = true;
        this.closeSocket("book voice assistant tts timeout");
        reject(new Error("AI 语音连接超时"));
      }, connectTimeoutMs);
      const clearConnectTimer = () => {
        if (connectTimer !== null) {
          clearTimeout(connectTimer);
          connectTimer = null;
        }
      };
      const settle = (callback: () => void) => {
        clearConnectTimer();
        callback();
      };
      const socketTask = wx.connectSocket({
        url,
        header: cookie ? { Cookie: cookie } : {},
        fail: (error) => {
          settle(() => reject(new Error(error.errMsg || "AI 语音连接失败")));
        },
      });
      this.socketTask = socketTask;
      socketTask.onOpen(() => {
        if (this.closed) {
          socketTask.close({ code: 1000, reason: "book voice assistant tts closed" });
          return;
        }
        clearConnectTimer();
        this.socketOpen = true;
      });
      socketTask.onMessage((message) => {
        if (this.closed) {
          return;
        }
        this.messageChain = this.messageChain
          .then(() => {
            if (this.closed) {
              return undefined;
            }
            return this.handleSocketMessage(message.data, startPayload, settle);
          })
          .catch((error) => {
            if (this.closed) {
              return;
            }
            settle(() => reject(error instanceof Error ? error : new Error("AI 语音处理失败")));
            this.closeSocket("book voice assistant tts failed");
          });
      });
      socketTask.onError((error) => {
        if (this.closed) {
          return;
        }
        settle(() => reject(new Error(error.errMsg || "AI 语音连接失败")));
      });
      socketTask.onClose(() => {
        this.socketOpen = false;
        if (!this.closed) {
          settle(() => reject(new Error("AI 语音连接已关闭")));
        }
      });
    });
    if (terminalEvent.type === "tts.completed") {
      return terminalEvent;
    }
    throw new Error(terminalEvent.error || (terminalEvent.type === "tts.cancelled" ? "AI 语音已取消" : "AI 语音失败"));
  }

  cancel(): void {
    if (this.closed) {
      return;
    }
    this.sendJson({ type: "tts.cancel" });
    this.resolveStart?.({
      type: "tts.cancelled",
      project_id: this.projectId,
      state: "cancelled",
      error: "AI 语音已取消",
    });
    this.closeSocket("book voice assistant tts cancelled");
  }

  close(): void {
    if (!this.closed) {
      this.resolveStart?.({
        type: "tts.cancelled",
        project_id: this.projectId,
        state: "cancelled",
        error: "AI 语音已取消",
      });
    }
    this.closeSocket("book voice assistant tts closed");
  }

  private startPayload(options: BookVoiceAssistantTTSStartOptions): Record<string, unknown> {
    return {
      type: "tts.start",
      text: options.text,
      voice: options.voice || undefined,
      speed_ratio: options.speedRatio === 1 ? undefined : options.speedRatio,
      audio_protocol: "pcm_frame",
    };
  }

  private async handleSocketMessage(
    data: string | ArrayBuffer,
    startPayload: Record<string, unknown>,
    settle: (callback: () => void) => void,
  ): Promise<void> {
    const event = safeJsonParse(typeof data === "string" ? data : "");
    if (!event || typeof event.type !== "string") {
      return;
    }
    if (event.type === "tts.ready") {
      this.sendStart(startPayload);
      return;
    }
    if (event.type === "tts.started") {
      this.callbacks.onStarted?.(event as unknown as BookVoiceAssistantTTSStartedEvent);
      return;
    }
    if (event.type === "audio.format") {
      this.callbacks.onAudioFormat?.(event as unknown as BookVoiceAssistantTTSAudioFormatEvent);
      return;
    }
    if (event.type === "audio.frame") {
      this.handleAudioFrame(event as unknown as BookVoiceAssistantTTSAudioFrameEvent);
      return;
    }
    if (event.type === "audio.done") {
      this.callbacks.onAudioDone?.(event as unknown as BookVoiceAssistantTTSAudioDoneEvent);
      return;
    }
    if (event.type === "tts.completed") {
      const completed = event as unknown as BookVoiceAssistantTTSTerminalEvent;
      this.callbacks.onCompleted?.(completed);
      settle(() => this.resolveStart?.(completed));
      this.closeSocket("book voice assistant tts completed");
      return;
    }
    if (event.type === "tts.cancelled") {
      const cancelled = event as unknown as BookVoiceAssistantTTSTerminalEvent;
      this.callbacks.onCancelled?.(cancelled);
      settle(() => this.resolveStart?.(cancelled));
      this.closeSocket("book voice assistant tts cancelled");
      return;
    }
    if (event.type === "tts.failed") {
      const failed = event as unknown as BookVoiceAssistantTTSTerminalEvent;
      this.callbacks.onFailed?.(failed);
      settle(() => this.rejectStart?.(new Error(failed.error || "AI 语音失败")));
      this.closeSocket("book voice assistant tts failed");
    }
  }

  private sendStart(payload: Record<string, unknown>): void {
    if (this.startSent) {
      return;
    }
    this.startSent = true;
    this.sendJson(payload);
  }

  private sendJson(payload: Record<string, unknown>): void {
    if (!this.socketTask || !this.socketOpen) {
      return;
    }
    this.socketTask.send({ data: JSON.stringify(payload) });
  }

  private closeSocket(reason: string): void {
    this.closed = true;
    this.socketTask?.close({ code: 1000, reason });
    this.socketTask = null;
    this.socketOpen = false;
  }

  private handleAudioFrame(event: BookVoiceAssistantTTSAudioFrameEvent): void {
    const audio = wx.base64ToArrayBuffer(event.audio || "");
    const bitsPerSample = positiveInteger(event.bits_per_sample);
    if (bitsPerSample !== 16) {
      throw new Error("AI 语音帧格式不支持");
    }
    this.callbacks.onAudioFrame?.({
      streamId: event.stream_id,
      sequence: event.sequence,
      audio,
      mimeType: event.mime_type,
      sampleRate: positiveInteger(event.sample_rate) || 16000,
      channels: positiveInteger(event.channels) || 1,
      bitsPerSample: 16,
    });
  }
}

function positiveInteger(value: unknown): number {
  const numeric = Number(value);
  return Number.isFinite(numeric) && numeric > 0 ? Math.floor(numeric) : 0;
}

function safeJsonParse(value: string): Record<string, unknown> | null {
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === "object" ? parsed as Record<string, unknown> : null;
  } catch {
    return null;
  }
}
