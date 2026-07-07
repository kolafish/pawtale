import { ApiError, cleanBaseUrl, readBackendConfig } from "./request";
import { arrayBufferToBase64, estimatePcm16Level } from "./memoir-streaming-asr-protocol";
import { backendSessionCookieHeader } from "./memoir-voice-api";
import { WechatRecorderResource } from "./recorder-resource";
import { wechatRecorderErrorMessage } from "./wechat-privacy";

const DEBUG_RECORDING_DURATION_MS = 30 * 60 * 1000;
const DEBUG_FRAME_SIZE_KB = 4;
const DEBUG_ENCODE_BIT_RATE = 96000;
const DEBUG_CONNECT_TIMEOUT_MS = 10000;

export type DebugSpeakerConnectionStatus =
  | "idle"
  | "connecting"
  | "ready"
  | "listening"
  | "stopping"
  | "closed"
  | "error";

export interface DebugSpeakerGatewayEvent {
  type: string;
  state?: string;
  status?: string;
  error?: string;
  segment_id?: string;
  speaker_id?: string;
  speaker_gender?: string | null;
  text?: string;
  start_ms?: number | null;
  end_ms?: number | null;
  occurred_start_at_ms?: number | null;
  occurred_end_at_ms?: number | null;
  confidence?: number | null;
  provider_event_type?: string;
  provider_payload?: Record<string, unknown>;
  message?: string;
}

export interface DebugSpeakerCaptureStats {
  frameCount: number;
  audioBytesSent: number;
  recordingElapsedMs: number;
  audioLevel: number;
}

export interface DebugSpeakerTranscribeCallbacks {
  onStatus(status: DebugSpeakerConnectionStatus): void;
  onStats(stats: DebugSpeakerCaptureStats): void;
  onSpeakerEvent(event: DebugSpeakerGatewayEvent): void;
  onDiarizationStatus?(event: DebugSpeakerGatewayEvent): void;
  onError(message: string): void;
}

interface DebugSpeakerClientEvent {
  type: "session.start" | "audio.frame" | "audio.end" | "session.stop";
  audio?: string;
}

let debugClientSerial = 0;

export function debugSpeakerTranscribeGatewayUrl(): string {
  const config = readBackendConfig();
  if (!config.wsBaseUrl) {
    throw new ApiError(0, "未配置后端 WebSocket 地址", {});
  }
  return `${cleanBaseUrl(config.wsBaseUrl)}/api/mobile/memoir/debug/speaker-transcribe/gateway`;
}

export function normalizeDebugSpeakerGatewayEvent(raw: unknown): DebugSpeakerGatewayEvent {
  if (!raw || typeof raw !== "object") {
    return { type: "session.error", error: "invalid_debug_speaker_event" };
  }
  const event = raw as DebugSpeakerGatewayEvent;
  return {
    ...event,
    type: typeof event.type === "string" ? event.type : "session.error",
  };
}

export function debugSpeakerTitle(speakerId: string, speakerIndex: number): string {
  return speakerId === "speaker_unknown" ? "未分辨说话人" : `说话人 ${speakerIndex}`;
}

export class DebugSpeakerTranscribeClient {
  private socketTask: WechatMiniprogram.SocketTask | null = null;
  private socketOpen = false;
  private recorderStarted = false;
  private acceptingFrames = false;
  private stopping = false;
  private frameCount = 0;
  private audioBytesSent = 0;
  private captureStartedAtMs = 0;
  private readonly recorderOwnerId: string;

  constructor(
    private readonly callbacks: DebugSpeakerTranscribeCallbacks,
    private readonly recorderResource: WechatRecorderResource = new WechatRecorderResource(),
  ) {
    debugClientSerial += 1;
    this.recorderOwnerId = `debug-speaker-transcribe-${debugClientSerial}`;
  }

  async start(): Promise<void> {
    if (this.socketOpen || this.recorderStarted || this.acceptingFrames) {
      return;
    }
    this.callbacks.onStatus("connecting");
    const cookie = await backendSessionCookieHeader();
    const gatewayUrl = debugSpeakerTranscribeGatewayUrl();
    await new Promise<void>((resolve, reject) => {
      let settled = false;
      let timedOut = false;
      const timer = setTimeout(() => {
        timedOut = true;
        this.closeSocket();
        reject(new Error("多人转写连接超时"));
      }, DEBUG_CONNECT_TIMEOUT_MS);
      const settle = (callback: () => void) => {
        if (settled || timedOut) {
          return;
        }
        settled = true;
        clearTimeout(timer);
        callback();
      };
      const socketTask = wx.connectSocket({
        url: gatewayUrl,
        header: cookie ? { Cookie: cookie } : {},
        success: () => undefined,
        fail: (error) => {
          settle(() => reject(new Error(error.errMsg || "多人转写连接失败")));
        },
      });
      this.socketTask = socketTask;
      socketTask.onOpen(() => {
        this.socketOpen = true;
        this.callbacks.onStatus("ready");
        const started = this.startRecorder();
        if (!started) {
          this.closeSocket();
          settle(() => reject(new Error("麦克风正在使用中")));
          return;
        }
        settle(resolve);
      });
      socketTask.onMessage((message) => {
        this.handleSocketMessage(message.data);
      });
      socketTask.onError((error) => {
        const message = error.errMsg || "多人转写连接错误";
        settle(() => reject(new Error(message)));
        this.closeSocket();
        this.callbacks.onError(message);
        this.callbacks.onStatus("error");
      });
      socketTask.onClose(() => {
        this.socketOpen = false;
        if (!this.stopping) {
          this.releaseRecorder();
          this.callbacks.onStatus("closed");
        }
      });
    });
  }

  stop(): void {
    if (this.stopping) {
      return;
    }
    this.stopping = true;
    this.callbacks.onStatus("stopping");
    if (this.recorderStarted || this.recorderResource.isOwnedBy(this.recorderOwnerId)) {
      this.recorderResource.stop(this.recorderOwnerId);
      return;
    }
    this.sendJson({ type: "session.stop" });
  }

  close(): void {
    this.stopping = true;
    if (this.recorderStarted || this.recorderResource.isOwnedBy(this.recorderOwnerId)) {
      this.recorderResource.stop(this.recorderOwnerId);
    }
    this.sendJson({ type: "session.stop" });
    this.closeSocket();
  }

  private startRecorder(): boolean {
    this.frameCount = 0;
    this.audioBytesSent = 0;
    this.captureStartedAtMs = Date.now();
    try {
      return this.recorderResource.start(this.recorderOwnerId, {
        duration: DEBUG_RECORDING_DURATION_MS,
        sampleRate: 16000,
        numberOfChannels: 1,
        encodeBitRate: DEBUG_ENCODE_BIT_RATE,
        format: "PCM",
        frameSize: DEBUG_FRAME_SIZE_KB,
      }, {
        onStart: () => this.handleRecorderStarted(),
        onFrame: (result) => this.handleFrame(result.frameBuffer, result.isLastFrame),
        onError: (message) => this.handleRecorderError(message),
        onStop: () => this.handleRecorderStopped(),
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : "录音启动失败";
      this.callbacks.onError(message);
      this.callbacks.onStatus("error");
      return false;
    }
  }

  private handleRecorderStarted(): void {
    this.recorderStarted = true;
    this.acceptingFrames = true;
    this.sendJson({ type: "session.start" });
    this.callbacks.onStatus("listening");
    this.emitStats(0);
  }

  private handleFrame(frameBuffer: ArrayBuffer, isLastFrame: boolean): void {
    if (!this.socketOpen || !this.acceptingFrames || this.stopping) {
      return;
    }
    if (frameBuffer.byteLength > 0) {
      this.frameCount += 1;
      this.audioBytesSent += frameBuffer.byteLength;
      this.emitStats(estimatePcm16Level(frameBuffer));
      this.sendJson({
        type: "audio.frame",
        audio: arrayBufferToBase64(frameBuffer),
      });
    }
    if (isLastFrame) {
      this.stop();
    }
  }

  private handleRecorderStopped(): void {
    this.recorderStarted = false;
    this.acceptingFrames = false;
    this.sendJson({ type: "session.stop" });
  }

  private handleRecorderError(message: string): void {
    const readableMessage = wechatRecorderErrorMessage(message);
    this.recorderStarted = false;
    this.acceptingFrames = false;
    this.stopping = true;
    this.sendJson({ type: "session.stop" });
    this.callbacks.onError(readableMessage);
    this.callbacks.onStatus("error");
  }

  private handleSocketMessage(data: string | ArrayBuffer): void {
    const raw = typeof data === "string" ? safeJsonParse(data) : null;
    const event = normalizeDebugSpeakerGatewayEvent(raw);
    if (event.type === "session.ready") {
      this.callbacks.onStatus(this.acceptingFrames ? "listening" : "ready");
      return;
    }
    if (event.type === "session.started") {
      this.callbacks.onStatus("listening");
      return;
    }
    if (event.type === "speaker.partial" || event.type === "speaker.final" || event.type === "speaker.reassign") {
      this.callbacks.onSpeakerEvent(this.eventWithOccurrenceTime(event));
      return;
    }
    if (event.type === "speaker.diarization") {
      this.callbacks.onDiarizationStatus?.(event);
      return;
    }
    if (event.type === "session.stopped") {
      this.closeSocket();
      this.callbacks.onStatus("closed");
      return;
    }
    if (event.type === "session.error") {
      const message = event.error || "多人转写服务异常";
      this.closeSocket();
      this.callbacks.onError(message);
      this.callbacks.onStatus("error");
    }
  }

  private emitStats(audioLevel: number): void {
    this.callbacks.onStats({
      frameCount: this.frameCount,
      audioBytesSent: this.audioBytesSent,
      recordingElapsedMs: this.captureStartedAtMs ? Date.now() - this.captureStartedAtMs : 0,
      audioLevel,
    });
  }

  private eventWithOccurrenceTime(event: DebugSpeakerGatewayEvent): DebugSpeakerGatewayEvent {
    if (!this.captureStartedAtMs) {
      return event;
    }
    return {
      ...event,
      occurred_start_at_ms: typeof event.start_ms === "number"
        ? this.captureStartedAtMs + event.start_ms
        : null,
      occurred_end_at_ms: typeof event.end_ms === "number"
        ? this.captureStartedAtMs + event.end_ms
        : null,
    };
  }

  private sendJson(event: DebugSpeakerClientEvent): void {
    if (!this.socketTask || !this.socketOpen) {
      return;
    }
    this.socketTask.send({
      data: JSON.stringify(event),
      fail: (error) => {
        this.callbacks.onError(error.errMsg || "多人转写数据发送失败");
      },
    });
  }

  private closeSocket(): void {
    this.releaseRecorder();
    this.socketTask?.close({ code: 1000, reason: "debug speaker transcribe closed" });
    this.socketTask = null;
    this.socketOpen = false;
  }

  private releaseRecorder(): void {
    if (this.recorderStarted || this.recorderResource.isOwnedBy(this.recorderOwnerId)) {
      this.recorderResource.stop(this.recorderOwnerId);
    }
    this.recorderStarted = false;
    this.acceptingFrames = false;
  }
}

function safeJsonParse(data: string): unknown {
  try {
    return JSON.parse(data);
  } catch {
    return null;
  }
}
