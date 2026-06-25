import { backendSessionCookieHeader, bookVoiceAssistantTTSStreamUrl } from "./memoir-voice-api";
import {
  type PreparedStreamingTTSAudioSegment,
  StreamingTTSSegmentAssembler,
} from "./streaming-tts-segments";

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
}

export interface BookVoiceAssistantTTSTerminalEvent {
  type: "tts.completed" | "tts.failed" | "tts.cancelled";
  stream_id?: string;
  project_id?: string;
  state?: string;
  error?: string;
}

export interface BookVoiceAssistantTTSCallbacks {
  onStarted?(event: BookVoiceAssistantTTSStartedEvent): void;
  onAudioSegment?(segment: PreparedStreamingTTSAudioSegment): void;
  onCompleted?(event: BookVoiceAssistantTTSTerminalEvent): void;
  onCancelled?(event: BookVoiceAssistantTTSTerminalEvent): void;
  onFailed?(event: BookVoiceAssistantTTSTerminalEvent): void;
}

export class BookVoiceAssistantTTSStreamClient {
  private socketTask: WechatMiniprogram.SocketTask | null = null;
  private socketOpen = false;
  private closed = false;
  private startSent = false;
  private segmentAssembler: StreamingTTSSegmentAssembler | null = null;
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
    this.segmentAssembler = new StreamingTTSSegmentAssembler(`book-voice-assistant-${this.projectId}-${Date.now()}`);
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

  disposeTempFiles(): void {
    this.segmentAssembler?.disposeTempFiles();
  }

  private startPayload(options: BookVoiceAssistantTTSStartOptions): Record<string, unknown> {
    return {
      type: "tts.start",
      text: options.text,
      voice: options.voice || undefined,
      speed_ratio: options.speedRatio === 1 ? undefined : options.speedRatio,
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
    if (event.type === "text.segment") {
      this.segmentAssembler?.rememberText(event as unknown as { segment_index: number; text: string });
      return;
    }
    if (event.type === "audio.chunk") {
      this.segmentAssembler?.appendChunk(event as unknown as {
        segment_index: number;
        audio: string;
        mime_type: string;
        sample_rate?: number | null;
        channels?: number | null;
        bits_per_sample?: number | null;
      });
      return;
    }
    if (event.type === "audio.segment_done") {
      const segment = await this.segmentAssembler?.finalize(event as unknown as {
        segment_index: number;
        duration_seconds?: number | null;
      });
      if (segment) {
        this.callbacks.onAudioSegment?.(segment);
      }
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
}

function safeJsonParse(value: string): Record<string, unknown> | null {
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === "object" ? parsed as Record<string, unknown> : null;
  } catch {
    return null;
  }
}
