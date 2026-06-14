import type {
  MemoirGuidanceMode,
  VoiceGatewayClientEvent,
  VoiceGatewayServerEvent,
} from "../domain/voice-chat";
import { voiceBackendCookieHeader, voiceGatewayUrl } from "./memoir-voice-api";

const STREAMING_RECORDING_DURATION_MS = 10 * 60 * 1000;
const STREAMING_FRAME_SIZE_KB = 4;
const recorderManager = wx.getRecorderManager();

let activeClient: MemoirStreamingAsrClient | null = null;
let recorderEventsBound = false;

export type VoiceChatConnectionStatus =
  | "idle"
  | "connecting"
  | "ready"
  | "listening"
  | "stopping"
  | "closed"
  | "error";

export interface MemoirStreamingAsrCallbacks {
  onStatus(status: VoiceChatConnectionStatus): void;
  onEvent(event: VoiceGatewayServerEvent): void;
  onPartial(text: string): void;
  onFinal(event: VoiceGatewayServerEvent): void;
  onError(message: string): void;
}

export interface MemoirStreamingAsrOptions {
  voiceSessionId: string;
  guidanceMode: MemoirGuidanceMode;
  currentGuidanceModule?: string;
  callbacks: MemoirStreamingAsrCallbacks;
}

export function arrayBufferToBase64(buffer: ArrayBuffer): string {
  const chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  const bytes = new Uint8Array(buffer);
  let output = "";
  let index = 0;
  while (index < bytes.length) {
    const first = bytes[index++];
    const second = index < bytes.length ? bytes[index++] : Number.NaN;
    const third = index < bytes.length ? bytes[index++] : Number.NaN;
    output += chars[first >> 2];
    output += chars[((first & 0x03) << 4) | ((second || 0) >> 4)];
    output += Number.isNaN(second) ? "=" : chars[((second & 0x0F) << 2) | ((third || 0) >> 6)];
    output += Number.isNaN(third) ? "=" : chars[third & 0x3F];
  }
  return output;
}

export function normalizeVoiceGatewayEvent(raw: unknown): VoiceGatewayServerEvent {
  if (!raw || typeof raw !== "object") {
    return { type: "session.error", error: "invalid_gateway_event" };
  }
  const event = raw as VoiceGatewayServerEvent;
  return {
    ...event,
    type: typeof event.type === "string" ? event.type : "session.error",
  };
}

export class MemoirStreamingAsrClient {
  private socketTask: WechatMiniprogram.SocketTask | null = null;
  private socketOpen = false;
  private recording = false;
  private closed = false;

  constructor(private readonly options: MemoirStreamingAsrOptions) {}

  async connect(): Promise<void> {
    this.closed = false;
    this.options.callbacks.onStatus("connecting");
    const cookie = await voiceBackendCookieHeader();
    await new Promise<void>((resolve, reject) => {
      const socketTask = wx.connectSocket({
        url: voiceGatewayUrl(this.options.voiceSessionId),
        header: cookie ? { Cookie: cookie } : {},
        success: () => undefined,
        fail: (error) => reject(new Error(error.errMsg || "语音连接失败")),
      });
      this.socketTask = socketTask;
      socketTask.onOpen(() => {
        this.socketOpen = true;
        this.sendJson({
          type: "session.start",
          guidance_mode: this.options.guidanceMode,
          current_guidance_module: this.options.currentGuidanceModule,
        });
        resolve();
      });
      socketTask.onMessage((message) => {
        this.handleSocketMessage(message.data);
      });
      socketTask.onError((error) => {
        const message = error.errMsg || "语音连接错误";
        this.options.callbacks.onError(message);
        this.options.callbacks.onStatus("error");
      });
      socketTask.onClose(() => {
        this.socketOpen = false;
        if (!this.closed) {
          this.options.callbacks.onStatus("closed");
        }
      });
    });
  }

  startRecording(): void {
    if (!this.socketOpen || this.recording) {
      return;
    }
    bindRecorderEvents();
    activeClient = this;
    this.recording = true;
    this.options.callbacks.onStatus("listening");
    recorderManager.start({
      duration: STREAMING_RECORDING_DURATION_MS,
      sampleRate: 16000,
      numberOfChannels: 1,
      encodeBitRate: 256000,
      format: "PCM",
      frameSize: STREAMING_FRAME_SIZE_KB,
    });
  }

  stopRecording(): void {
    if (!this.recording) {
      return;
    }
    this.recording = false;
    this.options.callbacks.onStatus("stopping");
    recorderManager.stop();
    this.sendJson({ type: "audio.end" });
  }

  close(): void {
    this.closed = true;
    if (this.recording) {
      this.stopRecording();
    }
    this.sendJson({ type: "session.stop" });
    this.socketTask?.close({ code: 1000, reason: "voice chat closed" });
    this.socketTask = null;
    this.socketOpen = false;
    if (activeClient === this) {
      activeClient = null;
    }
    this.options.callbacks.onStatus("closed");
  }

  handleFrame(frameBuffer: ArrayBuffer, isLastFrame: boolean): void {
    if (!this.socketOpen || !this.recording) {
      return;
    }
    if (frameBuffer.byteLength > 0) {
      this.sendJson({
        type: "audio.frame",
        audio: arrayBufferToBase64(frameBuffer),
      });
    }
    if (isLastFrame) {
      this.sendJson({ type: "audio.end" });
    }
  }

  handleRecorderError(message: string): void {
    this.recording = false;
    this.options.callbacks.onError(message);
    this.options.callbacks.onStatus("error");
  }

  private handleSocketMessage(data: string | ArrayBuffer): void {
    const raw = typeof data === "string" ? safeJsonParse(data) : null;
    const event = normalizeVoiceGatewayEvent(raw);
    this.options.callbacks.onEvent(event);
    if (event.type === "session.ready" || event.type === "session.started") {
      this.options.callbacks.onStatus("ready");
      return;
    }
    if (event.type === "asr.partial") {
      this.options.callbacks.onPartial(event.text || "");
      return;
    }
    if (event.type === "asr.final") {
      this.options.callbacks.onFinal(event);
      return;
    }
    if (event.type === "session.error") {
      this.options.callbacks.onError(event.error || "语音识别失败");
      this.options.callbacks.onStatus("error");
    }
  }

  private sendJson(event: VoiceGatewayClientEvent): void {
    if (!this.socketTask || !this.socketOpen) {
      return;
    }
    this.socketTask.send({
      data: JSON.stringify(event),
      fail: (error) => {
        this.options.callbacks.onError(error.errMsg || "语音数据发送失败");
      },
    });
  }
}

function bindRecorderEvents(): void {
  if (recorderEventsBound) {
    return;
  }
  recorderManager.onFrameRecorded((result) => {
    activeClient?.handleFrame(result.frameBuffer, result.isLastFrame);
  });
  recorderManager.onError((error) => {
    activeClient?.handleRecorderError(error.errMsg || "录音失败");
  });
  recorderManager.onStop(() => {
    if (activeClient) {
      activeClient = null;
    }
  });
  recorderEventsBound = true;
}

function safeJsonParse(data: string): unknown {
  try {
    return JSON.parse(data);
  } catch {
    return null;
  }
}
