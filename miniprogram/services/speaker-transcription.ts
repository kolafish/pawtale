import { ApiError, cleanBaseUrl, readBackendConfig } from "./request";
import { arrayBufferToBase64, estimatePcm16Level } from "./memoir-streaming-asr-protocol";
import { backendSessionCookieHeader } from "./memoir-voice-session-api";
import { sharedRecorderResource, WechatRecorderResource } from "./recorder-resource";
import { wechatRecorderErrorMessage } from "./wechat-privacy";

const SPEAKER_TRANSCRIPTION_RECORDING_DURATION_MS = 30 * 60 * 1000;
const SPEAKER_TRANSCRIPTION_FRAME_SIZE_KB = 4;
const SPEAKER_TRANSCRIPTION_ENCODE_BIT_RATE = 96000;
const SPEAKER_TRANSCRIPTION_CONNECT_TIMEOUT_MS = 10000;

export type SpeakerTranscriptionConnectionStatus =
  | "idle"
  | "connecting"
  | "ready"
  | "listening"
  | "stopping"
  | "closed"
  | "error";

export interface SpeakerTranscriptionGatewayEvent {
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
  message?: string;
}

export interface SpeakerTranscriptionCallbacks {
  onStatus(status: SpeakerTranscriptionConnectionStatus): void;
  onAudioLevel(audioLevel: number): void;
  onSpeakerEvent(event: SpeakerTranscriptionGatewayEvent): void;
  onDiarizationStatus?(event: SpeakerTranscriptionGatewayEvent): void;
  onError(message: string): void;
}

interface SpeakerTranscriptionClientEvent {
  type: "session.start" | "audio.frame" | "session.stop";
  audio?: string;
}

let speakerTranscriptionClientSerial = 0;

export function speakerTranscriptionGatewayUrl(): string {
  const config = readBackendConfig();
  if (!config.wsBaseUrl) {
    throw new ApiError(0, "未配置后端 WebSocket 地址", {});
  }
  return `${cleanBaseUrl(config.wsBaseUrl)}/api/mobile/memoir/speaker-transcription/gateway`;
}

export function normalizeSpeakerTranscriptionGatewayEvent(raw: unknown): SpeakerTranscriptionGatewayEvent {
  if (!raw || typeof raw !== "object") {
    return { type: "session.error", error: "invalid_speaker_transcription_event" };
  }
  const event = raw as SpeakerTranscriptionGatewayEvent;
  return {
    ...event,
    type: typeof event.type === "string" ? event.type : "session.error",
  };
}

export function speakerTitle(speakerId: string, speakerIndex: number): string {
  return speakerId === "speaker_unknown" ? "未分辨说话人" : `说话人 ${speakerIndex}`;
}

export class SpeakerTranscriptionClient {
  private socketTask: WechatMiniprogram.SocketTask | null = null;
  private socketOpen = false;
  private recorderStarted = false;
  private acceptingFrames = false;
  private stopping = false;
  private closed = false;
  private cancelPendingStart: (() => void) | null = null;
  private captureStartedAtMs = 0;
  private readonly recorderOwnerId: string;

  constructor(
    private readonly callbacks: SpeakerTranscriptionCallbacks,
    private readonly recorderResource: WechatRecorderResource = sharedRecorderResource,
  ) {
    speakerTranscriptionClientSerial += 1;
    this.recorderOwnerId = `speaker-transcription-${speakerTranscriptionClientSerial}`;
  }

  async start(): Promise<void> {
    if (this.closed) {
      throw new Error("多人转写连接已取消");
    }
    if (this.socketOpen || this.recorderStarted || this.acceptingFrames) {
      return;
    }
    this.callbacks.onStatus("connecting");
    const cookie = await backendSessionCookieHeader();
    if (this.closed) {
      throw new Error("多人转写连接已取消");
    }
    const gatewayUrl = speakerTranscriptionGatewayUrl();
    await new Promise<void>((resolve, reject) => {
      let settled = false;
      let timer: number | null = null;
      const settle = (callback: () => void) => {
        if (settled) {
          return;
        }
        settled = true;
        if (timer !== null) {
          clearTimeout(timer);
        }
        this.cancelPendingStart = null;
        callback();
      };
      timer = setTimeout(() => {
        settle(() => {
          this.closeSocket();
          reject(new Error("多人转写连接超时"));
        });
      }, SPEAKER_TRANSCRIPTION_CONNECT_TIMEOUT_MS);
      this.cancelPendingStart = () => {
        settle(() => reject(new Error("多人转写连接已取消")));
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
        if (this.socketTask !== socketTask) {
          socketTask.close({ code: 1000, reason: "stale speaker transcription socket" });
          return;
        }
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
        if (this.socketTask !== socketTask) {
          return;
        }
        this.handleSocketMessage(message.data);
      });
      socketTask.onError((error) => {
        if (this.socketTask !== socketTask) {
          return;
        }
        const message = error.errMsg || "多人转写连接错误";
        settle(() => reject(new Error(message)));
        this.closed = true;
        this.closeSocket();
        this.callbacks.onError(message);
        this.callbacks.onStatus("error");
      });
      socketTask.onClose(() => {
        if (this.socketTask !== socketTask) {
          return;
        }
        const connecting = !settled;
        this.closed = true;
        this.socketTask = null;
        this.socketOpen = false;
        this.releaseRecorder();
        if (connecting) {
          settle(() => reject(new Error("多人转写连接已断开")));
        } else {
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
    this.closed = true;
    this.stopping = true;
    this.cancelPendingStart?.();
    if (this.recorderStarted || this.recorderResource.isOwnedBy(this.recorderOwnerId)) {
      this.recorderResource.stop(this.recorderOwnerId);
    }
    this.sendJson({ type: "session.stop" });
    this.closeSocket();
  }

  private startRecorder(): boolean {
    this.captureStartedAtMs = Date.now();
    try {
      return this.recorderResource.start(this.recorderOwnerId, {
        duration: SPEAKER_TRANSCRIPTION_RECORDING_DURATION_MS,
        sampleRate: 16000,
        numberOfChannels: 1,
        encodeBitRate: SPEAKER_TRANSCRIPTION_ENCODE_BIT_RATE,
        format: "PCM",
        frameSize: SPEAKER_TRANSCRIPTION_FRAME_SIZE_KB,
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
    if (this.closed || !this.socketOpen) {
      this.releaseRecorder();
      return;
    }
    this.recorderStarted = true;
    this.acceptingFrames = true;
    this.sendJson({ type: "session.start" });
    this.callbacks.onStatus("listening");
    this.callbacks.onAudioLevel(0);
  }

  private handleFrame(frameBuffer: ArrayBuffer, isLastFrame: boolean): void {
    if (!this.socketOpen || !this.acceptingFrames || this.stopping) {
      return;
    }
    if (frameBuffer.byteLength > 0) {
      this.callbacks.onAudioLevel(estimatePcm16Level(frameBuffer));
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
    const event = normalizeSpeakerTranscriptionGatewayEvent(raw);
    if (event.type === "session.ready") {
      this.callbacks.onStatus(this.acceptingFrames ? "listening" : "ready");
      return;
    }
    if (event.type === "session.started") {
      this.callbacks.onStatus("listening");
      return;
    }
    if (event.type === "speaker.partial" || event.type === "speaker.final") {
      this.callbacks.onSpeakerEvent(this.eventWithOccurrenceTime(event));
      return;
    }
    if (event.type === "speaker.diarization") {
      this.callbacks.onDiarizationStatus?.(event);
      return;
    }
    if (event.type === "session.stopped") {
      this.closed = true;
      this.closeSocket();
      this.callbacks.onStatus("closed");
      return;
    }
    if (event.type === "session.error") {
      const message = event.error || "多人转写服务异常";
      this.closed = true;
      this.closeSocket();
      this.callbacks.onError(message);
      this.callbacks.onStatus("error");
    }
  }

  private eventWithOccurrenceTime(event: SpeakerTranscriptionGatewayEvent): SpeakerTranscriptionGatewayEvent {
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

  private sendJson(event: SpeakerTranscriptionClientEvent): void {
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
    const socketTask = this.socketTask;
    this.socketTask = null;
    this.socketOpen = false;
    this.releaseRecorder();
    socketTask?.close({ code: 1000, reason: "speaker transcription closed" });
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
