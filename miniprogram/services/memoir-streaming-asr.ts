import type {
  MemoirGuidanceMode,
  MemoirSpeakerRole,
  VoiceChatErrorInfo,
  VoiceGatewayState,
  VoiceGatewayClientEvent,
  VoiceGatewayServerEvent,
} from "../domain/voice-chat";
import { backendSessionCookieHeader, voiceGatewayUrl } from "./memoir-voice-api";
import {
  arrayBufferToBase64,
  classifyVoiceClientConnectionError,
  classifyVoiceGatewayError,
  clientError,
  normalizeVoiceGatewayEvent,
} from "./memoir-streaming-asr-protocol";
import { sharedRecorderResource, WechatRecorderResource } from "./recorder-resource";
import { VoiceActivityDetector } from "./voice-activity-detector";
import { wechatRecorderErrorMessage } from "./wechat-privacy";

export {
  arrayBufferToBase64,
  classifyVoiceClientConnectionError,
  classifyVoiceGatewayError,
  estimatePcm16Level,
  normalizeVoiceGatewayEvent,
} from "./memoir-streaming-asr-protocol";

const STREAMING_RECORDING_DURATION_MS = 10 * 60 * 1000;
const STREAMING_FRAME_SIZE_KB = 4;
const STREAMING_ENCODE_BIT_RATE = 96000;
const SOCKET_CONNECT_TIMEOUT_MS = 10000;
const AUDIO_END_TIMEOUT_MS = 15000;
const SESSION_STOP_TIMEOUT_MS = 8000;
const RECORDER_START_TIMEOUT_MS = 4000;
const DEFAULT_SILENCE_LEVEL_THRESHOLD = 0.015;
const DEFAULT_MIN_AUTO_STOP_RECORDING_MS = 1200;
const MIN_VOICE_ACTIVITY_MS = 240;

let activeClient: MemoirStreamingAsrClient | null = null;
let clientSerial = 0;

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
  onClientEvent?(event: MemoirStreamingAsrClientEvent): void;
}

export type MemoirCaptureState = "idle" | "opening" | "recording" | "stopping" | "error";
export type MemoirAutoStopReason = "silence_after_voice" | "no_voice";
export type MemoirStreamingAsrClientEventType =
  | "connection_timeout"
  | "socket_connect_fail"
  | "socket_open"
  | "socket_error"
  | "socket_close"
  | "socket_send_fail"
  | "recorder_error"
  | "recording_auto_stop"
  | "audio_end_sent"
  | "wait_timeout";

export interface MemoirCaptureStats {
  state: MemoirCaptureState;
  frameCount: number;
  audioBytesSent: number;
  recordingElapsedMs: number;
  gatewayState?: VoiceGatewayState;
  audioLevel: number;
  silentElapsedMs: number;
  autoStopped: boolean;
  autoStopReason: MemoirAutoStopReason | "";
}

export interface MemoirStreamingAsrClientEvent {
  type: MemoirStreamingAsrClientEventType;
  message?: string;
  nativeMessage?: string;
  gatewayEventType?: string;
  timeoutMs?: number;
  frameCount: number;
  audioBytesSent: number;
  recordingElapsedMs: number;
  gatewayState?: VoiceGatewayState;
  autoStopped: boolean;
  autoStopReason: MemoirAutoStopReason | "";
  errorKind?: VoiceChatErrorInfo["kind"];
  errorCode?: string;
}

export interface MemoirStreamingAsrOptions {
  voiceSessionId: string;
  gatewayUrl?: string;
  guidanceMode: MemoirGuidanceMode;
  speakerRole?: MemoirSpeakerRole;
  currentGuidanceModule?: string;
  autoStopSilenceMs?: number;
  noVoiceActivityTimeoutMs?: number;
  connectTimeoutMs?: number;
  silenceLevelThreshold?: number;
  minAutoStopRecordingMs?: number;
  callbacks: MemoirStreamingAsrCallbacks;
}

export interface MemoirStartRecordingOptions {
  noVoiceActivityTimeoutMs?: number;
  speakerRole?: MemoirSpeakerRole;
  parentMessageId?: string;
}

export class MemoirStreamingAsrClient {
  private readonly options: MemoirStreamingAsrOptions;
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
  private audioLevel = 0;
  private silentElapsedMs = 0;
  private voiceActivityElapsedMs = 0;
  private hasVoiceActivity = false;
  private voiceActivityDetector: VoiceActivityDetector;
  private autoStopped = false;
  private autoStopReason: MemoirAutoStopReason | "" = "";
  private currentSpeakerRole: MemoirSpeakerRole = "narrator";
  private currentParentMessageId = "";
  private noVoiceActivityTimeoutMs = 0;
  private gatewayState: VoiceGatewayState | undefined;
  private noVoiceActivityTimer: ReturnType<typeof setTimeout> | null = null;
  private recorderStartTimer: ReturnType<typeof setTimeout> | null = null;
  private closePromise: Promise<void> | null = null;
  private waiters: Array<{
    eventType: string;
    resolve: () => void;
    reject: (error: Error) => void;
  }> = [];
  private readonly recorderOwnerId: string;

  constructor(
    options: MemoirStreamingAsrOptions,
    private readonly recorderResource: WechatRecorderResource = sharedRecorderResource,
  ) {
    this.options = options;
    clientSerial += 1;
    this.recorderOwnerId = `memoir-streaming-asr-${clientSerial}`;
    this.voiceActivityDetector = this.createVoiceActivityDetector();
  }

  configureAutoStop(autoStopSilenceMs: number): void {
    this.options.autoStopSilenceMs = Math.max(0, autoStopSilenceMs);
    if (!this.options.autoStopSilenceMs) {
      this.silentElapsedMs = 0;
      this.voiceActivityElapsedMs = 0;
      this.hasVoiceActivity = false;
      this.voiceActivityDetector.reset();
      this.autoStopped = false;
      this.autoStopReason = "";
      this.clearNoVoiceActivityTimer();
    }
  }

  async connect(): Promise<void> {
    this.closed = false;
    this.options.callbacks.onStatus("connecting");
    const connectTimeoutMs = this.options.connectTimeoutMs ?? SOCKET_CONNECT_TIMEOUT_MS;
    let timedOut = false;
    let timeoutTimer: ReturnType<typeof setTimeout> | null = null;
    const timeoutPromise = new Promise<never>((_, reject) => {
      timeoutTimer = setTimeout(() => {
        timedOut = true;
        this.closed = true;
        this.socketOpen = false;
        this.emitClientEvent("connection_timeout", {
          message: "语音连接超时",
          timeoutMs: connectTimeoutMs,
        });
        this.socketTask?.close({ code: 1000, reason: "voice connection timeout" });
        reject(new Error("语音连接超时"));
      }, connectTimeoutMs);
    });
    const connectPromise = (async () => {
      const cookie = await backendSessionCookieHeader();
      if (timedOut || this.closed) {
        throw new Error("语音连接超时");
      }
      const gatewayUrl = this.options.gatewayUrl || voiceGatewayUrl(this.options.voiceSessionId);
      await new Promise<void>((resolve, reject) => {
        let settled = false;
        const settle = (callback: () => void) => {
          if (settled || timedOut) {
            return;
          }
          settled = true;
          callback();
        };
        const socketTask = wx.connectSocket({
          url: gatewayUrl,
          header: cookie ? { Cookie: cookie } : {},
          success: () => undefined,
          fail: (error) => {
            const message = error.errMsg || "语音连接失败";
            this.emitClientEvent("socket_connect_fail", {
              message,
              nativeMessage: error.errMsg,
              errorKind: "connection",
            });
            settle(() => reject(new Error(message)));
          },
        });
        this.socketTask = socketTask;
        socketTask.onOpen(() => {
          if (timedOut || this.closed) {
            socketTask.close({ code: 1000, reason: "voice connection timeout" });
            return;
          }
          this.socketOpen = true;
          this.emitClientEvent("socket_open");
          this.options.callbacks.onStatus("ready");
          this.emitCaptureStats("idle");
          settle(resolve);
        });
        socketTask.onMessage((message) => {
          if (!timedOut && !this.closed) {
            this.handleSocketMessage(message.data);
          }
        });
        socketTask.onError((error) => {
          if (timedOut || this.closed) {
            return;
          }
          const errorInfo = classifyVoiceClientConnectionError(error.errMsg || "语音连接错误");
          this.emitClientEvent("socket_error", {
            message: errorInfo.message,
            nativeMessage: error.errMsg,
            errorKind: errorInfo.kind,
            errorCode: errorInfo.code,
          });
          settle(() => reject(new Error(errorInfo.message)));
          this.rejectWaiters(errorInfo.message);
          this.options.callbacks.onError(errorInfo.message, errorInfo);
          this.options.callbacks.onStatus("error");
        });
        socketTask.onClose((closeEvent) => {
          const closeDetail = closeEvent as { reason?: string; errMsg?: string } | undefined;
          this.socketOpen = false;
          this.emitClientEvent("socket_close", {
            message: "语音连接已关闭",
            nativeMessage: closeDetail?.reason || closeDetail?.errMsg || "",
          });
          settle(() => reject(new Error("语音连接已关闭")));
          this.rejectWaiters("语音连接已关闭");
          if (!this.closed) {
            this.stopRecorderAfterGatewayEnd("idle");
            this.options.callbacks.onStatus("closed");
          }
        });
      });
    })();
    try {
      await Promise.race([connectPromise, timeoutPromise]);
    } finally {
      if (timeoutTimer) {
        clearTimeout(timeoutTimer);
      }
    }
  }

  startRecording(recordingOptions: MemoirStartRecordingOptions = {}): boolean {
    if (!this.socketOpen || this.recording) {
      return false;
    }
    activeClient = this;
    this.recording = true;
    this.recorderStarted = false;
    this.acceptingFrames = false;
    this.audioEndSent = false;
    this.audioEnded = false;
    this.sessionStopped = false;
    this.frameCount = 0;
    this.audioBytesSent = 0;
    this.audioLevel = 0;
    this.silentElapsedMs = 0;
    this.voiceActivityElapsedMs = 0;
    this.hasVoiceActivity = false;
    this.voiceActivityDetector = this.createVoiceActivityDetector();
    this.autoStopped = false;
    this.autoStopReason = "";
    this.currentSpeakerRole = recordingOptions.speakerRole || this.options.speakerRole || "narrator";
    this.currentParentMessageId = recordingOptions.parentMessageId || "";
    this.noVoiceActivityTimeoutMs = Math.max(
      0,
      recordingOptions.noVoiceActivityTimeoutMs ?? this.options.noVoiceActivityTimeoutMs ?? 0,
    );
    this.captureStartedAtMs = Date.now();
    this.emitCaptureStats("opening");
    try {
      const started = this.recorderResource.start(this.recorderOwnerId, {
        duration: STREAMING_RECORDING_DURATION_MS,
        sampleRate: 16000,
        numberOfChannels: 1,
        encodeBitRate: STREAMING_ENCODE_BIT_RATE,
        format: "PCM",
        frameSize: STREAMING_FRAME_SIZE_KB,
      }, {
        onStart: () => {
          this.handleRecorderStarted();
        },
        onFrame: (result) => {
          this.handleFrame(result.frameBuffer, result.isLastFrame);
        },
        onError: (message) => {
          this.handleRecorderError(message);
        },
        onStop: () => {
          this.handleRecorderStopped();
        },
      });
      if (!started) {
        this.handleRecorderError("麦克风正在使用中");
        return false;
      }
      this.startRecorderStartTimer();
      this.startNoVoiceActivityTimer();
    } catch (error) {
      this.clearRecorderStartTimer();
      this.clearNoVoiceActivityTimer();
      const message = error instanceof Error ? error.message : "录音启动失败";
      this.handleRecorderError(message);
      return false;
    }
    return true;
  }

  stopRecording(): void {
    if (!this.recording) {
      return;
    }
    this.clearRecorderStartTimer();
    this.clearNoVoiceActivityTimer();
    this.recording = false;
    this.options.callbacks.onStatus("stopping");
    if (this.recorderStarted) {
      this.emitCaptureStats("stopping");
      this.recorderResource.stop(this.recorderOwnerId);
    } else {
      this.acceptingFrames = false;
      this.emitCaptureStats("idle");
      this.recorderResource.forceRelease(this.recorderOwnerId);
      if (activeClient === this) {
        activeClient = null;
      }
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
      this.updateVoiceActivity(frameBuffer);
      const autoStopReason = this.autoStopReasonForCurrentInput();
      if (autoStopReason) {
        this.autoStopped = true;
        this.autoStopReason = autoStopReason;
      }
      this.emitCaptureStats("recording");
      this.sendJson({
        type: "audio.frame",
        audio: arrayBufferToBase64(frameBuffer),
      });
      if (autoStopReason) {
        this.emitClientEvent("recording_auto_stop", {
          message: autoStopReason === "no_voice" ? "未检测到说话，自动停止录音" : "检测到停顿，自动停止录音",
        });
        this.stopRecording();
        return;
      }
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
      if (!this.recording && activeClient === this) {
        activeClient = null;
      }
      return;
    }
    this.finishAudioInput();
    if (!this.recording && activeClient === this) {
      activeClient = null;
    }
  }

  handleRecorderError(message: string): void {
    const readableMessage = wechatRecorderErrorMessage(message);
    if (message && message !== readableMessage) {
      console.warn("recorder native error", message);
    }
    this.clearRecorderStartTimer();
    this.recording = false;
    this.recorderStarted = false;
    this.clearNoVoiceActivityTimer();
    if (this.acceptingFrames && !this.audioEndSent) {
      this.finishAudioInput();
    }
    this.acceptingFrames = false;
    this.emitCaptureStats("error");
    this.rejectWaiters(readableMessage);
    this.emitClientEvent("recorder_error", {
      message: readableMessage,
      nativeMessage: message,
      errorKind: "recorder",
    });
    this.options.callbacks.onError(readableMessage, clientError(readableMessage, "recorder"));
    this.options.callbacks.onStatus("error");
    if (activeClient === this) {
      activeClient = null;
    }
  }

  handleRecorderStarted(): void {
    if (activeClient !== this) {
      return;
    }
    this.clearRecorderStartTimer();
    this.recorderStarted = true;
    if (!this.recording) {
      this.emitCaptureStats("stopping");
      this.recorderResource.stop(this.recorderOwnerId);
      return;
    }
    this.acceptingFrames = true;
    this.sendJson({
      type: "session.start",
      guidance_mode: this.options.guidanceMode,
      current_guidance_module: this.options.currentGuidanceModule,
      speaker_role: this.currentSpeakerRole,
      parent_message_id: this.currentParentMessageId || undefined,
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
    this.clearRecorderStartTimer();
    this.clearNoVoiceActivityTimer();
    if (this.recorderStarted) {
      this.recorderResource.stop(this.recorderOwnerId);
    }
    this.recorderStarted = false;
    this.emitCaptureStats(captureState);
  }

  private finishAudioInput(): void {
    if (this.audioEndSent) {
      return;
    }
    this.clearRecorderStartTimer();
    this.clearNoVoiceActivityTimer();
    this.audioEndSent = true;
    this.acceptingFrames = false;
    this.silentElapsedMs = 0;
    this.options.callbacks.onStatus("stopping");
    this.emitCaptureStats("stopping");
    this.emitClientEvent("audio_end_sent", { gatewayEventType: "audio.end" });
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
        const message = eventType === "audio.ended" ? "语音保存超时" : "本次语音记录保存超时";
        this.emitClientEvent("wait_timeout", {
          message,
          gatewayEventType: eventType,
          timeoutMs,
        });
        reject(new Error(message));
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
    this.clearRecorderStartTimer();
    this.clearNoVoiceActivityTimer();
    this.socketTask?.close({ code: 1000, reason: "voice chat closed" });
    this.socketTask = null;
    this.socketOpen = false;
    this.recording = false;
    if (this.recorderStarted || this.recorderResource.isOwnedBy(this.recorderOwnerId)) {
      this.recorderResource.stop(this.recorderOwnerId);
    }
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
      audioLevel: this.audioLevel,
      silentElapsedMs: this.silentElapsedMs,
      autoStopped: this.autoStopped,
      autoStopReason: this.autoStopReason,
    });
  }

  private emitClientEvent(
    type: MemoirStreamingAsrClientEventType,
    detail: Partial<MemoirStreamingAsrClientEvent> = {},
  ): void {
    this.options.callbacks.onClientEvent?.({
      type,
      frameCount: this.frameCount,
      audioBytesSent: this.audioBytesSent,
      recordingElapsedMs: this.captureStartedAtMs ? Date.now() - this.captureStartedAtMs : 0,
      gatewayState: this.gatewayState,
      autoStopped: this.autoStopped,
      autoStopReason: this.autoStopReason,
      ...detail,
    });
  }

  private updateVoiceActivity(frameBuffer: ArrayBuffer): void {
    const state = this.voiceActivityDetector.update(frameBuffer);
    this.audioLevel = state.audioLevel;
    this.silentElapsedMs = state.silentElapsedMs;
    this.voiceActivityElapsedMs = state.voiceActivityElapsedMs;
    const hadVoiceActivity = this.hasVoiceActivity;
    this.hasVoiceActivity = state.hasVoiceActivity;
    if (!hadVoiceActivity && this.hasVoiceActivity) {
      this.clearNoVoiceActivityTimer();
    }
  }

  private createVoiceActivityDetector(): VoiceActivityDetector {
    return new VoiceActivityDetector({
      silenceLevelThreshold: this.options.silenceLevelThreshold ?? DEFAULT_SILENCE_LEVEL_THRESHOLD,
      minVoiceActivityMs: MIN_VOICE_ACTIVITY_MS,
      trackPreVoiceSilence: true,
    });
  }

  private startNoVoiceActivityTimer(): void {
    this.clearNoVoiceActivityTimer();
    if (this.noVoiceActivityTimeoutMs <= 0) {
      return;
    }
    this.noVoiceActivityTimer = setTimeout(() => {
      this.noVoiceActivityTimer = null;
      this.handleNoVoiceActivityTimeout();
    }, this.noVoiceActivityTimeoutMs);
  }

  private clearNoVoiceActivityTimer(): void {
    if (this.noVoiceActivityTimer) {
      clearTimeout(this.noVoiceActivityTimer);
      this.noVoiceActivityTimer = null;
    }
  }

  private startRecorderStartTimer(): void {
    this.clearRecorderStartTimer();
    this.recorderStartTimer = setTimeout(() => {
      this.recorderStartTimer = null;
      if (!this.recording || this.recorderStarted) {
        return;
      }
      this.recorderResource.forceRelease(this.recorderOwnerId);
      this.handleRecorderError("麦克风启动超时，请重新开始录音");
    }, RECORDER_START_TIMEOUT_MS);
  }

  private clearRecorderStartTimer(): void {
    if (this.recorderStartTimer) {
      clearTimeout(this.recorderStartTimer);
      this.recorderStartTimer = null;
    }
  }

  private handleNoVoiceActivityTimeout(): void {
    if (!this.recording || this.audioEndSent || this.autoStopped || this.hasVoiceActivity) {
      return;
    }
    this.autoStopped = true;
    this.autoStopReason = "no_voice";
    this.emitCaptureStats(this.acceptingFrames ? "recording" : "opening");
    this.emitClientEvent("recording_auto_stop", {
      message: "未检测到说话，自动停止录音",
    });
    this.stopRecording();
  }

  private autoStopReasonForCurrentInput(): MemoirAutoStopReason | "" {
    if (this.audioEndSent || this.autoStopped) {
      return "";
    }
    if (!this.hasVoiceActivity) {
      return this.noVoiceActivityTimeoutMs > 0 && this.silentElapsedMs >= this.noVoiceActivityTimeoutMs
        ? "no_voice"
        : "";
    }
    const silenceMs = this.options.autoStopSilenceMs || 0;
    if (silenceMs <= 0) {
      return "";
    }
    const elapsedMs = this.captureStartedAtMs ? Date.now() - this.captureStartedAtMs : 0;
    const minElapsedMs = this.options.minAutoStopRecordingMs ?? DEFAULT_MIN_AUTO_STOP_RECORDING_MS;
    return elapsedMs >= minElapsedMs && this.silentElapsedMs >= silenceMs
      ? "silence_after_voice"
      : "";
  }

  private sendJson(event: VoiceGatewayClientEvent): void {
    if (!this.socketTask || !this.socketOpen) {
      return;
    }
    this.socketTask.send({
      data: JSON.stringify(event),
      fail: (error) => {
        const message = error.errMsg || "语音数据发送失败";
        this.emitClientEvent("socket_send_fail", {
          message,
          nativeMessage: error.errMsg,
          gatewayEventType: event.type,
          errorKind: "connection",
        });
        this.options.callbacks.onError(message, clientError(message, "connection"));
      },
    });
  }
}

function safeJsonParse(data: string): unknown {
  try {
    return JSON.parse(data);
  } catch {
    return null;
  }
}
