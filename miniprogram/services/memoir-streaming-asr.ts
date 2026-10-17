import type {
  MemoirGuidanceMode,
  MemoirSpeakerRole,
  VoiceChatErrorInfo,
  VoiceGatewayState,
  VoiceGatewayClientEvent,
  VoiceGatewayServerEvent,
} from "../domain/memoir-voice-session";
import { backendSessionCookieHeader, voiceGatewayUrl } from "./memoir-voice-session-api";
import { readSelectedAIModel } from "./ai-model-settings";
import { readSelectedTranscriptPolishLevel } from "./transcript-polish-settings";
import {
  arrayBufferToBase64,
  classifyVoiceClientConnectionError,
  classifyVoiceGatewayError,
  clientError,
  normalizeVoiceGatewayEvent,
} from "./memoir-streaming-asr-protocol";
import { sharedRecorderResource, WechatRecorderResource } from "./recorder-resource";
import { VoiceActivityDetector } from "./voice-activity-detector";
import {
  isRecorderPermissionDeniedMessage,
  wechatRecorderErrorMessage,
} from "./wechat-privacy";

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
const BARGE_IN_PREFIX_BYTES = 16000 * 2 * 2;
const BARGE_IN_PREPARE_TIMEOUT_MS = 10000;
const BARGE_IN_MAX_PENDING_BYTES = 16000 * 2 * 12;

export interface MemoirRecorderConfiguration {
  capturePurpose: "barge_in" | "utterance";
  platform: string;
  wechatVersion: string;
  sdkVersion: string;
  nativeVoiceChatSupported: boolean | null;
  requestedAudioMode: "voiceChat" | "default";
}

function recorderConfiguration(
  capturePurpose: MemoirRecorderConfiguration["capturePurpose"],
): MemoirRecorderConfiguration {
  const app = wx.getAppBaseInfo?.();
  const sdkVersion = app?.SDKVersion || "";
  const version = sdkVersion.match(/^(\d+)\.(\d+)\.(\d+)$/);
  const [major, minor, patch] = version ? version.slice(1).map(Number) : [0, 0, 0];
  const sdkSupported = major > 3 || (major === 3 && (minor > 17 || (minor === 17 && patch >= 3)));
  const nativeVoiceChatSupported = wx.canIUse?.("RecorderManager.start.object.audioMode.voiceChat") ?? null;
  return {
    capturePurpose,
    platform: wx.getDeviceInfo?.().platform || "",
    wechatVersion: app?.version || "",
    sdkVersion,
    nativeVoiceChatSupported,
    requestedAudioMode: sdkSupported && nativeVoiceChatSupported === true ? "voiceChat" : "default",
  };
}

export interface BargeInCallbacks {
  onBargeIn(): void;
  onError(message: string): void;
}

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
  | "recorder_start_requested"
  | "barge_in_unavailable"
  | "barge_in_confirmed"
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
  recorderConfiguration?: MemoirRecorderConfiguration;
  bargeInStartMs?: number;
  retainedAudioMs?: number;
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
  maxDurationMs?: number;
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
  private bargeInMode: "" | "monitoring" | "pending" = "";
  private bargeInId = "";
  private bargeInSerial = 0;
  private bargeInCallbacks: BargeInCallbacks | null = null;
  private bargeInFrames: ArrayBuffer[] = [];
  private bargeInBytes = 0;
  private bargeInTotalBytes = 0;
  private bargeInPrepareTimer: ReturnType<typeof setTimeout> | null = null;

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

  isBargeInMonitoring(): boolean {
    return Boolean(this.bargeInMode);
  }

  startBargeInMonitoring(callbacks: BargeInCallbacks): boolean {
    if (!this.socketOpen || this.recording || this.bargeInMode || this.acceptingFrames) return false;
    const configuration = recorderConfiguration("barge_in");
    if (configuration.requestedAudioMode !== "voiceChat") {
      this.emitClientEvent("barge_in_unavailable", { recorderConfiguration: configuration });
      callbacks.onError("当前微信版本不支持自动打断，请点按钮回答");
      return false;
    }
    if (this.recorderResource.isBusy()) {
      callbacks.onError("麦克风正在使用中");
      return false;
    }
    this.bargeInMode = "monitoring";
    this.bargeInId = `${this.recorderOwnerId}-${++this.bargeInSerial}`;
    this.bargeInCallbacks = callbacks;
    activeClient = this;
    this.sendJson({ type: "barge_in.start", monitor_id: this.bargeInId });
    if (!this.socketOpen) return false;
    return this.startNativeRecorder(STREAMING_RECORDING_DURATION_MS);
  }

  async stopBargeInMonitoring(discardPending = false): Promise<void> {
    // During the automatic interrupt, cancelling playback must keep the same
    // recorder alive until consent sync and session.start have completed.
    if (!this.bargeInMode) {
      // Playback cancellation can already have requested a native stop. A
      // subsequent manual-recording request must await that same stop.
      if (!this.recording) await this.recorderResource.stopAndWait(this.recorderOwnerId);
      return;
    }
    if (this.bargeInMode === "pending" && !discardPending) return;
    this.sendJson({ type: "barge_in.stop", monitor_id: this.bargeInId });
    this.resetBargeIn();
    this.clearRecorderStartTimer();
    await this.recorderResource.stopAndWait(this.recorderOwnerId);
    this.recorderStarted = false;
  }

  private resetBargeIn(): void {
    if (this.bargeInPrepareTimer) clearTimeout(this.bargeInPrepareTimer);
    this.bargeInPrepareTimer = null;
    this.bargeInMode = "";
    this.bargeInId = "";
    this.bargeInFrames = [];
    this.bargeInBytes = 0;
    this.bargeInTotalBytes = 0;
    this.bargeInCallbacks = null;
  }

  private failBargeIn(message: string): void {
    const callbacks = this.bargeInCallbacks;
    const pending = this.bargeInMode === "pending";
    void this.stopBargeInMonitoring(true);
    callbacks?.onError(message);
    if (pending) this.options.callbacks.onError(message, clientError(message, "recorder"));
  }

  startRecording(recordingOptions: MemoirStartRecordingOptions = {}): boolean {
    if (!this.socketOpen || this.recording) {
      return false;
    }
    const continuingCapture = this.bargeInMode === "pending" && this.recorderStarted;
    if (this.bargeInMode && !continuingCapture) return false;
    const prefix = continuingCapture ? this.bargeInFrames : [];
    this.resetBargeIn();
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
    if (continuingCapture) {
      this.handleRecorderStarted();
      for (const frame of prefix) this.handleFrame(frame, false);
      this.startNoVoiceActivityTimer();
      return this.recording;
    }
    const started = this.startNativeRecorder(recordingOptions.maxDurationMs ?? STREAMING_RECORDING_DURATION_MS);
    if (started) this.startNoVoiceActivityTimer();
    return started;
  }

  private startNativeRecorder(maxDurationMs: number): boolean {
    this.recorderStarted = false;
    try {
      const configuration = recorderConfiguration(this.bargeInMode === "monitoring" ? "barge_in" : "utterance");
      this.emitClientEvent("recorder_start_requested", { recorderConfiguration: configuration });
      const started = this.recorderResource.start(this.recorderOwnerId, {
        duration: Math.min(STREAMING_RECORDING_DURATION_MS, Math.max(1000, maxDurationMs)),
        sampleRate: 16000,
        numberOfChannels: 1,
        encodeBitRate: STREAMING_ENCODE_BIT_RATE,
        format: "PCM",
        frameSize: STREAMING_FRAME_SIZE_KB,
        ...(configuration.requestedAudioMode === "voiceChat" ? { audioMode: "voiceChat" as const } : {}),
      }, {
        onStart: () => this.handleRecorderStarted(),
        onFrame: (result) => this.handleFrame(result.frameBuffer, result.isLastFrame),
        onError: (message) => this.handleRecorderError(message),
        onStop: () => this.handleRecorderStopped(),
      });
      if (!started) {
        this.handleRecorderError("麦克风正在使用中");
        return false;
      }
      this.startRecorderStartTimer();
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
    if (this.bargeInMode) await this.stopBargeInMonitoring(true);
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
    if (this.bargeInMode && this.socketOpen) {
      if (frameBuffer.byteLength) {
        this.bargeInFrames.push(frameBuffer.slice(0));
        this.bargeInBytes += frameBuffer.byteLength;
        this.bargeInTotalBytes += frameBuffer.byteLength;
        if (this.bargeInMode === "monitoring") {
          while (this.bargeInBytes > BARGE_IN_PREFIX_BYTES && this.bargeInFrames.length > 1) {
            this.bargeInBytes -= this.bargeInFrames.shift()!.byteLength;
          }
          this.sendJson({ type: "barge_in.frame", monitor_id: this.bargeInId,
            audio: arrayBufferToBase64(frameBuffer) });
        } else if (this.bargeInBytes > BARGE_IN_MAX_PENDING_BYTES) {
          this.failBargeIn("录音准备超时，请点按钮重新录音");
        }
      }
      if (isLastFrame && this.bargeInMode) this.failBargeIn("打断监听已停止，可以点按钮回答");
      return;
    }
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
    if (this.bargeInMode) {
      this.failBargeIn("打断监听已停止，可以点按钮回答");
      return;
    }
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
    if (this.bargeInMode) {
      this.failBargeIn(wechatRecorderErrorMessage(message));
      return;
    }
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
    this.options.callbacks.onError(readableMessage, clientError(
      readableMessage,
      "recorder",
      isRecorderPermissionDeniedMessage(message) ? "recorder_permission_denied" : "recorder_failed",
    ));
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
    if (this.bargeInMode) return;
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
      ai_model: readSelectedAIModel(),
      transcript_polish_level: readSelectedTranscriptPolishLevel(),
    });
    this.options.callbacks.onStatus("listening");
    this.emitCaptureStats("recording");
  }

  private handleSocketMessage(data: string | ArrayBuffer): void {
    const raw = typeof data === "string" ? safeJsonParse(data) : null;
    const event = normalizeVoiceGatewayEvent(raw);
    if (event.type.startsWith("barge_in.")) {
      if (this.bargeInMode !== "monitoring" || event.monitor_id !== this.bargeInId) return;
      if (event.type === "barge_in.error") this.failBargeIn("打断监听暂不可用，可以点按钮回答");
      if (event.type === "barge_in.speech") {
        const startMs = event.start_ms;
        const oldestByte = this.bargeInTotalBytes - this.bargeInBytes;
        const startByte = typeof startMs === "number" ? Math.floor(startMs * 16) * 2 : -1;
        if (!Number.isFinite(startByte) || startByte < oldestByte || startByte >= this.bargeInTotalBytes) {
          this.failBargeIn("语音连接延迟较大，可以点按钮回答");
          return;
        }
        let discardBytes = startByte - oldestByte;
        while (discardBytes > 0 && this.bargeInFrames.length) {
          const frame = this.bargeInFrames[0];
          const size = Math.min(discardBytes, frame.byteLength);
          if (size === frame.byteLength) this.bargeInFrames.shift();
          else this.bargeInFrames[0] = frame.slice(size);
          this.bargeInBytes -= size;
          discardBytes -= size;
        }
        this.bargeInMode = "pending";
        this.emitClientEvent("barge_in_confirmed", {
          bargeInStartMs: startByte / 32,
          retainedAudioMs: this.bargeInBytes / 32,
        });
        this.sendJson({ type: "barge_in.stop", monitor_id: this.bargeInId });
        if (!this.socketOpen) return;
        this.bargeInPrepareTimer = setTimeout(() => {
          this.failBargeIn("录音准备超时，请点按钮重新录音");
        }, BARGE_IN_PREPARE_TIMEOUT_MS);
        this.bargeInCallbacks?.onBargeIn();
      }
      return;
    }
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
    this.resetBargeIn();
    this.recording = false;
    this.acceptingFrames = false;
    this.clearRecorderStartTimer();
    this.clearNoVoiceActivityTimer();
    if (this.recorderStarted || this.recorderResource.isOwnedBy(this.recorderOwnerId)) {
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
    this.sendJson({ type: "audio.end", speech_detected: this.hasVoiceActivity });
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
    this.resetBargeIn();
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
      if ((!this.recording && !this.bargeInMode) || this.recorderStarted) {
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
        this.failConnectionFromSendError(message);
      },
    });
  }

  private failConnectionFromSendError(message: string): void {
    if (this.closed) {
      return;
    }
    this.closed = true;
    this.socketOpen = false;
    this.rejectWaiters(message);
    this.stopRecorderAfterGatewayEnd("error");
    this.socketTask?.close({ code: 1000, reason: "voice socket send failed" });
    this.socketTask = null;
    if (activeClient === this) {
      activeClient = null;
    }
    this.options.callbacks.onError(message, clientError(message, "connection"));
    this.options.callbacks.onStatus("error");
  }
}

function safeJsonParse(data: string): unknown {
  try {
    return JSON.parse(data);
  } catch {
    return null;
  }
}
