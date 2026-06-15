import type {
  MemoirGuidanceMode,
  VoiceChatErrorInfo,
  VoiceChatErrorKind,
  VoiceGatewayState,
  VoiceGatewayClientEvent,
  VoiceGatewayServerEvent,
} from "../domain/voice-chat";
import { backendSessionCookieHeader, voiceGatewayUrl } from "./memoir-voice-api";

const STREAMING_RECORDING_DURATION_MS = 10 * 60 * 1000;
const STREAMING_FRAME_SIZE_KB = 4;
const STREAMING_ENCODE_BIT_RATE = 96000;
const SOCKET_CONNECT_TIMEOUT_MS = 10000;
const AUDIO_END_TIMEOUT_MS = 15000;
const SESSION_STOP_TIMEOUT_MS = 8000;
const RECORDER_STOP_FALLBACK_MS = 1200;
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
  onCaptureStats?(stats: MemoirCaptureStats): void;
  onEvent(event: VoiceGatewayServerEvent): void;
  onPartial(text: string): void;
  onFinal(event: VoiceGatewayServerEvent): void;
  onError(message: string, error?: VoiceChatErrorInfo): void;
}

export type MemoirCaptureState = "idle" | "opening" | "recording" | "stopping" | "error";

export interface MemoirCaptureStats {
  state: MemoirCaptureState;
  frameCount: number;
  audioBytesSent: number;
  recordingElapsedMs: number;
  gatewayState?: VoiceGatewayState;
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

export function classifyVoiceGatewayError(event: VoiceGatewayServerEvent): VoiceChatErrorInfo {
  const code = event.error || "voice_gateway_error";
  const kind = errorKindFromCode(code);
  return {
    kind,
    code,
    state: event.state,
    message: messageFromErrorCode(code, kind),
  };
}

function errorKindFromCode(code: string): VoiceChatErrorKind {
  if (code.includes("persistence") || code.includes("sqlite") || code.includes("database")) {
    return "persistence";
  }
  if (code.includes("timeout") || code.includes("超时")) {
    return "timeout";
  }
  if (
    code.startsWith("audio_frame")
    || code.startsWith("audio_stream")
    || code.startsWith("unsupported_gateway")
    || code.startsWith("asr_session")
  ) {
    return "gateway";
  }
  if (code.startsWith("asr_provider") || code.startsWith("provider_") || code.startsWith("volc") || code.includes("asr_")) {
    return "asr";
  }
  return "unknown";
}

function messageFromErrorCode(code: string, kind: VoiceChatErrorKind): string {
  const messages: Partial<Record<string, string>> = {
    asr_final_persistence_failed: "转写已返回，但保存识别结果失败",
    asr_finish_timeout: "语音识别收尾超时",
    audio_frame_invalid_base64: "音频帧格式异常",
    audio_frame_missing_base64: "音频帧缺少数据",
    audio_frame_too_large: "单次音频数据过大",
    audio_stream_too_large: "本次录音超过可保存大小",
    asr_session_not_started: "语音识别还没有开始",
    asr_session_finishing: "上一句语音还在收尾",
    provider_empty_payload: "语音识别服务返回了空结果",
    unsupported_gateway_event: "语音连接事件不支持",
  };
  if (messages[code]) {
    return messages[code];
  }
  if (kind === "persistence") {
    return "识别结果保存失败";
  }
  if (kind === "asr") {
    return "语音识别服务异常";
  }
  if (kind === "gateway") {
    return "语音连接状态异常";
  }
  if (kind === "timeout") {
    return "语音处理超时";
  }
  return code || "语音处理失败";
}

function clientError(message: string, kind: VoiceChatErrorKind, code?: string): VoiceChatErrorInfo {
  return {
    kind,
    code,
    message,
  };
}

export class MemoirStreamingAsrClient {
  private socketTask: WechatMiniprogram.SocketTask | null = null;
  private socketOpen = false;
  private recording = false;
  private recorderStarted = false;
  private acceptingFrames = false;
  private audioEndSent = false;
  private audioEnded = false;
  private sessionStopped = false;
  private closed = false;
  private frameCount = 0;
  private audioBytesSent = 0;
  private captureStartedAtMs = 0;
  private gatewayState: VoiceGatewayState | undefined;
  private recorderStopTimer: ReturnType<typeof setTimeout> | null = null;
  private closePromise: Promise<void> | null = null;
  private waiters: Array<{
    eventType: string;
    resolve: () => void;
    reject: (error: Error) => void;
  }> = [];

  constructor(private readonly options: MemoirStreamingAsrOptions) {}

  async connect(): Promise<void> {
    this.closed = false;
    this.options.callbacks.onStatus("connecting");
    const cookie = await backendSessionCookieHeader();
    const gatewayUrl = voiceGatewayUrl(this.options.voiceSessionId);
    await new Promise<void>((resolve, reject) => {
      let settled = false;
      let connectTimer: ReturnType<typeof setTimeout>;
      const settle = (callback: () => void) => {
        if (settled) {
          return;
        }
        settled = true;
        clearTimeout(connectTimer);
        callback();
      };
      connectTimer = setTimeout(() => {
        settle(() => reject(new Error("语音连接超时")));
      }, SOCKET_CONNECT_TIMEOUT_MS);
      const socketTask = wx.connectSocket({
        url: gatewayUrl,
        header: cookie ? { Cookie: cookie } : {},
        success: () => undefined,
        fail: (error) => settle(() => reject(new Error(error.errMsg || "语音连接失败"))),
      });
      this.socketTask = socketTask;
      socketTask.onOpen(() => {
        this.socketOpen = true;
        settle(resolve);
      });
      socketTask.onMessage((message) => {
        this.handleSocketMessage(message.data);
      });
      socketTask.onError((error) => {
        const message = error.errMsg || "语音连接错误";
        settle(() => reject(new Error(message)));
        this.rejectWaiters(message);
        this.options.callbacks.onError(message, clientError(message, "connection"));
        this.options.callbacks.onStatus("error");
      });
      socketTask.onClose(() => {
        this.socketOpen = false;
        settle(() => reject(new Error("语音连接已关闭")));
        this.rejectWaiters("语音连接已关闭");
        if (!this.closed) {
          this.stopRecorderAfterGatewayEnd("idle");
          this.options.callbacks.onStatus("closed");
        }
      });
    });
  }

  startRecording(): boolean {
    if (!this.socketOpen || this.recording) {
      return false;
    }
    bindRecorderEvents();
    activeClient = this;
    this.recording = true;
    this.recorderStarted = false;
    this.acceptingFrames = false;
    this.audioEndSent = false;
    this.audioEnded = false;
    this.sessionStopped = false;
    this.frameCount = 0;
    this.audioBytesSent = 0;
    this.captureStartedAtMs = Date.now();
    this.emitCaptureStats("opening");
    try {
      recorderManager.start({
        duration: STREAMING_RECORDING_DURATION_MS,
        sampleRate: 16000,
        numberOfChannels: 1,
        encodeBitRate: STREAMING_ENCODE_BIT_RATE,
        format: "PCM",
        frameSize: STREAMING_FRAME_SIZE_KB,
      });
    } catch (error) {
      this.handleRecorderError(error instanceof Error ? error.message : "录音启动失败");
      return false;
    }
    return true;
  }

  stopRecording(): void {
    if (!this.recording) {
      return;
    }
    this.recording = false;
    this.options.callbacks.onStatus("stopping");
    if (this.recorderStarted) {
      this.emitCaptureStats("stopping");
      recorderManager.stop();
      this.recorderStopTimer = setTimeout(() => {
        this.finishAudioInput();
      }, RECORDER_STOP_FALLBACK_MS);
    } else {
      this.acceptingFrames = false;
      this.emitCaptureStats("idle");
    }
  }

  async finishSession(): Promise<void> {
    if (this.closed) {
      return;
    }
    this.options.callbacks.onStatus("stopping");
    if (this.recording) {
      this.stopRecording();
    }
    if (this.acceptingFrames || this.audioEndSent) {
      await this.waitForEvent("audio.ended", AUDIO_END_TIMEOUT_MS);
    }
    this.sendJson({ type: "session.stop" });
    await this.waitForEvent("session.stopped", SESSION_STOP_TIMEOUT_MS);
    this.closeSocket();
  }

  close(options: { force?: boolean } = {}): void {
    if (options.force) {
      if (this.recording) {
        this.stopRecording();
      }
      this.sendJson({ type: "session.stop" });
      this.closeSocket();
      return;
    }
    if (!this.socketOpen) {
      this.closeSocket();
      return;
    }
    if (this.closePromise) {
      return;
    }
    this.closePromise = this.finishSession().catch(() => {
      this.closeSocket();
    });
  }

  handleFrame(frameBuffer: ArrayBuffer, isLastFrame: boolean): void {
    if (!this.socketOpen || !this.recording || !this.acceptingFrames) {
      return;
    }
    if (frameBuffer.byteLength > 0) {
      this.frameCount += 1;
      this.audioBytesSent += frameBuffer.byteLength;
      this.emitCaptureStats("recording");
      this.sendJson({
        type: "audio.frame",
        audio: arrayBufferToBase64(frameBuffer),
      });
    }
    if (isLastFrame) {
      this.finishAudioInput();
    }
  }

  handleRecorderStopped(): void {
    this.recorderStarted = false;
    if (activeClient !== this || !this.acceptingFrames || this.audioEndSent) {
      if (!this.recording) {
        this.emitCaptureStats("idle");
      }
      return;
    }
    this.finishAudioInput();
  }

  handleRecorderError(message: string): void {
    this.recording = false;
    this.recorderStarted = false;
    if (this.acceptingFrames && !this.audioEndSent) {
      this.finishAudioInput();
    }
    this.acceptingFrames = false;
    this.emitCaptureStats("error");
    this.rejectWaiters(message);
    this.options.callbacks.onError(message, clientError(message, "recorder"));
    this.options.callbacks.onStatus("error");
  }

  handleRecorderStarted(): void {
    if (activeClient !== this) {
      return;
    }
    this.recorderStarted = true;
    if (!this.recording) {
      this.emitCaptureStats("stopping");
      try {
        recorderManager.stop();
      } catch {
        this.recorderStarted = false;
        this.emitCaptureStats("idle");
      }
      return;
    }
    this.acceptingFrames = true;
    this.sendJson({
      type: "session.start",
      guidance_mode: this.options.guidanceMode,
      current_guidance_module: this.options.currentGuidanceModule,
    });
    this.options.callbacks.onStatus("listening");
    this.emitCaptureStats("recording");
  }

  private handleSocketMessage(data: string | ArrayBuffer): void {
    const raw = typeof data === "string" ? safeJsonParse(data) : null;
    const event = normalizeVoiceGatewayEvent(raw);
    this.gatewayState = event.state || this.gatewayState;
    this.options.callbacks.onEvent(event);
    if (event.type === "session.ready" || event.type === "session.started") {
      this.options.callbacks.onStatus(this.acceptingFrames ? "listening" : "ready");
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
    if (event.type === "audio.ended") {
      this.audioEnded = true;
      this.acceptingFrames = false;
      this.resolveWaiters("audio.ended");
      this.options.callbacks.onStatus("ready");
      this.emitCaptureStats("idle");
      return;
    }
    if (event.type === "session.stopped") {
      this.sessionStopped = true;
      this.resolveWaiters("session.stopped");
      this.stopRecorderAfterGatewayEnd("idle");
      this.options.callbacks.onStatus("closed");
      return;
    }
    if (event.type === "session.error") {
      const error = classifyVoiceGatewayError(event);
      this.stopRecorderAfterGatewayEnd("error");
      this.rejectWaiters(error.message);
      this.options.callbacks.onError(error.message, error);
      this.options.callbacks.onStatus("error");
    }
  }

  private stopRecorderAfterGatewayEnd(captureState: MemoirCaptureState): void {
    this.recording = false;
    this.acceptingFrames = false;
    if (this.recorderStarted) {
      try {
        recorderManager.stop();
      } catch {
        // Recorder may already be stopped by WeChat after permission or system interruptions.
      }
    }
    this.recorderStarted = false;
    this.emitCaptureStats(captureState);
  }

  private finishAudioInput(): void {
    if (this.audioEndSent) {
      return;
    }
    if (this.recorderStopTimer) {
      clearTimeout(this.recorderStopTimer);
      this.recorderStopTimer = null;
    }
    this.audioEndSent = true;
    this.acceptingFrames = false;
    this.options.callbacks.onStatus("stopping");
    this.emitCaptureStats("stopping");
    this.sendJson({ type: "audio.end" });
  }

  private waitForEvent(eventType: string, timeoutMs: number): Promise<void> {
    if ((eventType === "audio.ended" && this.audioEnded) || (eventType === "session.stopped" && this.sessionStopped)) {
      return Promise.resolve();
    }
    return new Promise((resolve, reject) => {
      let waiterEntry: {
        eventType: string;
        resolve: () => void;
        reject: (error: Error) => void;
      };
      const timer = setTimeout(() => {
        this.waiters = this.waiters.filter((waiter) => waiter !== waiterEntry);
        reject(new Error(eventType === "audio.ended" ? "语音收尾超时" : "语音会话结束超时"));
      }, timeoutMs);
      waiterEntry = {
        eventType,
        resolve: () => {
          clearTimeout(timer);
          resolve();
        },
        reject: (error: Error) => {
          clearTimeout(timer);
          reject(error);
        },
      };
      this.waiters.push(waiterEntry);
    });
  }

  private resolveWaiters(eventType: string): void {
    const resolved = this.waiters.filter((waiter) => waiter.eventType === eventType);
    this.waiters = this.waiters.filter((waiter) => waiter.eventType !== eventType);
    resolved.forEach((waiter) => waiter.resolve());
  }

  private rejectWaiters(message: string): void {
    const waiters = this.waiters;
    this.waiters = [];
    waiters.forEach((waiter) => waiter.reject(new Error(message)));
  }

  private closeSocket(): void {
    this.closed = true;
    this.closePromise = null;
    this.rejectWaiters("语音连接已关闭");
    if (this.recorderStopTimer) {
      clearTimeout(this.recorderStopTimer);
      this.recorderStopTimer = null;
    }
    this.socketTask?.close({ code: 1000, reason: "voice chat closed" });
    this.socketTask = null;
    this.socketOpen = false;
    this.recording = false;
    this.recorderStarted = false;
    this.acceptingFrames = false;
    this.emitCaptureStats("idle");
    if (activeClient === this) {
      activeClient = null;
    }
    this.options.callbacks.onStatus("closed");
  }

  private emitCaptureStats(state: MemoirCaptureState): void {
    this.options.callbacks.onCaptureStats?.({
      state,
      frameCount: this.frameCount,
      audioBytesSent: this.audioBytesSent,
      recordingElapsedMs: this.captureStartedAtMs ? Date.now() - this.captureStartedAtMs : 0,
      gatewayState: this.gatewayState,
    });
  }

  private sendJson(event: VoiceGatewayClientEvent): void {
    if (!this.socketTask || !this.socketOpen) {
      return;
    }
    this.socketTask.send({
      data: JSON.stringify(event),
      fail: (error) => {
        const message = error.errMsg || "语音数据发送失败";
        this.options.callbacks.onError(message, clientError(message, "connection"));
      },
    });
  }
}

function bindRecorderEvents(): void {
  if (recorderEventsBound) {
    return;
  }
  recorderManager.onStart(() => {
    activeClient?.handleRecorderStarted();
  });
  recorderManager.onFrameRecorded((result) => {
    activeClient?.handleFrame(result.frameBuffer, result.isLastFrame);
  });
  recorderManager.onError((error) => {
    activeClient?.handleRecorderError(error.errMsg || "录音失败");
  });
  recorderManager.onStop(() => {
    activeClient?.handleRecorderStopped();
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
