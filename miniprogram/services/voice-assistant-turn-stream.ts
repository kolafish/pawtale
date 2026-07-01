import { readSelectedAIModel } from "./ai-model-settings";
import { assistantTurnStreamUrl, backendSessionCookieHeader } from "./memoir-voice-api";
import type {
  MemoirBookVoiceCommandExecuteResponse,
  MemoirBookVoiceCommandParseResponse,
} from "../domain/voice-chat";
import type { StreamingSpeechPCMFrame } from "./streaming-speech-playback";
import { readChatTTSSettings } from "./tts-settings";

const SOCKET_CONNECT_TIMEOUT_MS = 10000;
const TURN_ACTIVITY_TIMEOUT_MS = 30000;

export interface VoiceAssistantTurnStreamStartOptions {
  regenerate?: boolean;
  aiModel?: string;
  voice?: string;
  speedRatio?: number;
  extraPayload?: Record<string, unknown>;
  connectTimeoutMs?: number;
  turnActivityTimeoutMs?: number;
}

export interface VoiceAssistantTurnStreamClientOptions {
  streamUrl?: string;
}

export interface VoiceAssistantTurnStartedEvent {
  type: "turn.started";
  turn_id: string;
  voice_session_id: string;
  state: string;
  ai_provider?: string;
  ai_model?: string;
  tts_provider?: string;
  tts_model?: string;
  max_tokens?: number;
  audio_protocol?: string;
}

export interface VoiceAssistantTurnTextDeltaEvent {
  type: "text.delta";
  turn_id: string;
  voice_session_id: string;
  project_id?: string;
  text_delta: string;
  text: string;
}

interface VoiceAssistantTurnAudioMetadata {
  turn_id: string;
  voice_session_id: string;
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

export interface VoiceAssistantTurnAudioFormatEvent extends VoiceAssistantTurnAudioMetadata {
  type: "audio.format";
}

export interface VoiceAssistantTurnAudioFrameEvent extends VoiceAssistantTurnAudioMetadata {
  type: "audio.frame";
  audio: string;
}

export interface VoiceAssistantTurnAudioDoneEvent {
  type: "audio.done";
  turn_id: string;
  voice_session_id: string;
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

export interface VoiceAssistantTurnCompletedEvent {
  type: "turn.completed";
  turn_id: string;
  voice_session_id: string;
  project_id: string;
  state: "completed";
  message_id: string;
  audio_file_id?: string | null;
  text: string;
  primary_intent: string;
  intent_tags: string[];
  expects_answer: boolean;
  finish_reason?: string | null;
  usage?: Record<string, unknown>;
  progress_update?: Record<string, unknown> | null;
  progress_update_used?: boolean;
  progress_voice_selected_event_ids?: string[];
  command_preview?: MemoirBookVoiceCommandParseResponse | null;
  execution_result?: MemoirBookVoiceCommandExecuteResponse | null;
  turn_pipeline?: string;
}

export interface VoiceAssistantTurnCommandPreviewEvent {
  type: "command.preview";
  turn_id: string;
  voice_session_id: string;
  project_id: string;
  command_preview: MemoirBookVoiceCommandParseResponse;
  execute_after_reply?: boolean;
}

export interface VoiceAssistantTurnTerminalEvent {
  type: "turn.failed" | "turn.cancelled";
  turn_id?: string;
  voice_session_id?: string;
  state?: string;
  error?: string;
  reason?: string;
}

export interface PreparedAssistantStreamAudioFrame extends StreamingSpeechPCMFrame {
  turnId: string;
  sequence: number;
}

export interface VoiceAssistantTurnStreamCallbacks {
  onStarted?(event: VoiceAssistantTurnStartedEvent): void;
  onTextDelta?(event: VoiceAssistantTurnTextDeltaEvent): void;
  onAudioFormat?(event: VoiceAssistantTurnAudioFormatEvent): void;
  onAudioFrame?(frame: PreparedAssistantStreamAudioFrame): void;
  onAudioDone?(event: VoiceAssistantTurnAudioDoneEvent): void;
  onCommandPreview?(event: VoiceAssistantTurnCommandPreviewEvent): void;
  onCompleted?(event: VoiceAssistantTurnCompletedEvent): void;
  onCancelled?(event: VoiceAssistantTurnTerminalEvent): void;
  onFailed?(event: VoiceAssistantTurnTerminalEvent): void;
}

export class VoiceAssistantTurnStreamClient {
  private socketTask: WechatMiniprogram.SocketTask | null = null;
  private socketOpen = false;
  private closed = false;
  private startSent = false;
  private messageChain: Promise<void> = Promise.resolve();
  private connectTimer: ReturnType<typeof setTimeout> | null = null;
  private activityTimer: ReturnType<typeof setTimeout> | null = null;
  private resolveStart?: (event: VoiceAssistantTurnCompletedEvent | VoiceAssistantTurnTerminalEvent) => void;
  private rejectStart?: (error: Error) => void;

  constructor(
    private readonly voiceSessionId: string,
    private readonly callbacks: VoiceAssistantTurnStreamCallbacks = {},
    private readonly options: VoiceAssistantTurnStreamClientOptions = {},
  ) {}

  async start(options: VoiceAssistantTurnStreamStartOptions = {}): Promise<VoiceAssistantTurnCompletedEvent> {
    this.closed = false;
    this.startSent = false;
    this.socketOpen = false;
    this.socketTask = null;
    this.messageChain = Promise.resolve();
    this.clearTimers();
    const connectTimeoutMs = options.connectTimeoutMs ?? SOCKET_CONNECT_TIMEOUT_MS;
    const turnActivityTimeoutMs = options.turnActivityTimeoutMs ?? TURN_ACTIVITY_TIMEOUT_MS;
    const cookie = await backendSessionCookieHeader();
    const url = this.options.streamUrl || assistantTurnStreamUrl(this.voiceSessionId);
    const startPayload = this.startPayload(options);
    const terminalEvent = await new Promise<VoiceAssistantTurnCompletedEvent | VoiceAssistantTurnTerminalEvent>(
      (resolve, reject) => {
        this.resolveStart = resolve;
        this.rejectStart = reject;
        this.connectTimer = setTimeout(() => {
          this.connectTimer = null;
          if (this.closed || this.socketOpen) {
            return;
          }
          this.closed = true;
          this.closeSocket("assistant turn stream timeout");
          reject(new Error("AI 回应连接超时"));
        }, connectTimeoutMs);
        const resetActivityTimer = () => {
          this.resetActivityTimer(turnActivityTimeoutMs, () => {
            if (this.closed) {
              return;
            }
            this.closed = true;
            this.closeSocket("assistant turn activity timeout");
            reject(new Error("AI 回应等待超时"));
          });
        };
        const settle = (
          callback: () => void,
        ) => {
          this.clearTimers();
          callback();
        };
        const failStart = (message: string, reason: string) => {
          settle(() => reject(new Error(message)));
          this.closeSocket(reason);
        };
        const socketTask = wx.connectSocket({
          url,
          header: cookie ? { Cookie: cookie } : {},
          fail: (error) => {
            failStart(error.errMsg || "AI 回应连接失败", "assistant turn stream connect failed");
          },
        });
        this.socketTask = socketTask;
        socketTask.onOpen(() => {
          if (this.closed) {
            socketTask.close({ code: 1000, reason: "assistant turn stream closed" });
            return;
          }
          this.clearConnectTimer();
          this.socketOpen = true;
          resetActivityTimer();
        });
        socketTask.onMessage((message) => {
          if (!this.closed) {
            this.messageChain = this.messageChain
              .then(() => {
                if (this.closed) {
                  return undefined;
                }
                return this.handleSocketMessage(message.data, startPayload, settle, resetActivityTimer);
              })
              .catch((error) => {
                if (this.closed) {
                  return;
                }
                settle(() => reject(error instanceof Error ? error : new Error("AI 回应处理失败")));
                this.closeSocket("assistant turn stream failed");
              });
          }
        });
        socketTask.onError((error) => {
          if (this.closed) {
            return;
          }
          failStart(error.errMsg || "AI 回应连接失败", "assistant turn stream error");
        });
        socketTask.onClose(() => {
          this.socketOpen = false;
          if (!this.closed) {
            this.closed = true;
            this.socketTask = null;
            settle(() => reject(new Error("AI 回应连接已关闭")));
          }
        });
      },
    );
    if (terminalEvent.type === "turn.completed") {
      return terminalEvent;
    }
    throw new Error(terminalEvent.error || (terminalEvent.type === "turn.cancelled" ? "AI 回应已取消" : "AI 回应失败"));
  }

  cancel(reason = "cancelled"): void {
    if (this.closed) {
      return;
    }
    this.sendJson({ type: "turn.cancel", reason });
    this.resolveStart?.({
      type: "turn.cancelled",
      voice_session_id: this.voiceSessionId,
      state: reason === "interrupted_by_user" ? "interrupted" : "cancelled",
      reason,
      error: "AI 回应已取消",
    });
    this.closeSocket("assistant turn cancelled");
  }

  close(): void {
    if (!this.closed) {
      this.resolveStart?.({
        type: "turn.cancelled",
        voice_session_id: this.voiceSessionId,
        state: "cancelled",
        error: "AI 回应已取消",
      });
    }
    this.closeSocket("assistant turn closed");
  }

  private startPayload(options: VoiceAssistantTurnStreamStartOptions): Record<string, unknown> {
    const ttsSettings = readChatTTSSettings();
    const speedRatio = options.speedRatio ?? ttsSettings.speedRatio;
    return {
      ...(options.extraPayload || {}),
      type: "turn.start",
      ai_model: options.aiModel || readSelectedAIModel(),
      regenerate: options.regenerate || false,
      voice: options.voice || ttsSettings.voice || undefined,
      speed_ratio: speedRatio === 1 ? undefined : speedRatio,
      audio_protocol: "pcm_frame",
    };
  }

  private async handleSocketMessage(
    data: string | ArrayBuffer,
    startPayload: Record<string, unknown>,
    settle: (callback: () => void) => void,
    resetActivityTimer: () => void,
  ): Promise<void> {
    const event = safeJsonParse(typeof data === "string" ? data : "");
    if (!event || typeof event.type !== "string") {
      return;
    }
    resetActivityTimer();
    if (event.type === "turn.ready") {
      this.sendStart(startPayload);
      return;
    }
    if (event.type === "turn.started") {
      this.callbacks.onStarted?.(event as unknown as VoiceAssistantTurnStartedEvent);
      return;
    }
    if (event.type === "text.delta") {
      this.callbacks.onTextDelta?.(event as unknown as VoiceAssistantTurnTextDeltaEvent);
      return;
    }
    if (event.type === "audio.format") {
      this.callbacks.onAudioFormat?.(event as unknown as VoiceAssistantTurnAudioFormatEvent);
      return;
    }
    if (event.type === "audio.frame") {
      this.handleAudioFrame(event as unknown as VoiceAssistantTurnAudioFrameEvent);
      return;
    }
    if (event.type === "audio.done") {
      this.callbacks.onAudioDone?.(event as unknown as VoiceAssistantTurnAudioDoneEvent);
      return;
    }
    if (event.type === "command.preview") {
      this.callbacks.onCommandPreview?.(event as unknown as VoiceAssistantTurnCommandPreviewEvent);
      return;
    }
    if (event.type === "turn.completed") {
      const completed = event as unknown as VoiceAssistantTurnCompletedEvent;
      this.callbacks.onCompleted?.(completed);
      settle(() => this.resolveStart?.(completed));
      this.closeSocket("assistant turn completed");
      return;
    }
    if (event.type === "turn.cancelled") {
      const cancelled = event as unknown as VoiceAssistantTurnTerminalEvent;
      this.callbacks.onCancelled?.(cancelled);
      settle(() => this.resolveStart?.(cancelled));
      this.closeSocket("assistant turn cancelled");
      return;
    }
    if (event.type === "turn.failed") {
      const failed = event as unknown as VoiceAssistantTurnTerminalEvent;
      this.callbacks.onFailed?.(failed);
      settle(() => this.rejectStart?.(new Error(failed.error || "AI 回应失败")));
      this.closeSocket("assistant turn failed");
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
    this.clearTimers();
    this.closed = true;
    this.socketTask?.close({ code: 1000, reason });
    this.socketTask = null;
    this.socketOpen = false;
  }

  private resetActivityTimer(timeoutMs: number, onTimeout: () => void): void {
    this.clearActivityTimer();
    this.activityTimer = setTimeout(() => {
      this.activityTimer = null;
      onTimeout();
    }, timeoutMs);
  }

  private clearTimers(): void {
    this.clearConnectTimer();
    this.clearActivityTimer();
  }

  private clearConnectTimer(): void {
    if (this.connectTimer !== null) {
      clearTimeout(this.connectTimer);
      this.connectTimer = null;
    }
  }

  private clearActivityTimer(): void {
    if (this.activityTimer !== null) {
      clearTimeout(this.activityTimer);
      this.activityTimer = null;
    }
  }

  private handleAudioFrame(event: VoiceAssistantTurnAudioFrameEvent): void {
    const audio = wx.base64ToArrayBuffer(event.audio || "");
    const bitsPerSample = positiveInteger(event.bits_per_sample);
    if (bitsPerSample !== 16) {
      throw new Error("AI 语音帧格式不支持");
    }
    this.callbacks.onAudioFrame?.({
      turnId: event.turn_id,
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
