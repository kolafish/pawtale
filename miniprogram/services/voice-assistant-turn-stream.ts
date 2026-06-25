import { readSelectedAIModel } from "./ai-model-settings";
import { assistantTurnStreamUrl, backendSessionCookieHeader } from "./memoir-voice-api";
import {
  type PreparedStreamingTTSAudioSegment,
  StreamingTTSSegmentAssembler,
} from "./streaming-tts-segments";
import { readChatTTSSettings } from "./tts-settings";

const SOCKET_CONNECT_TIMEOUT_MS = 10000;

export interface VoiceAssistantTurnStreamStartOptions {
  regenerate?: boolean;
  aiModel?: string;
  voice?: string;
  speedRatio?: number;
  connectTimeoutMs?: number;
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
}

export interface VoiceAssistantTurnTextDeltaEvent {
  type: "text.delta";
  turn_id: string;
  voice_session_id: string;
  text_delta: string;
  text: string;
}

export interface VoiceAssistantTurnTextSegmentEvent {
  type: "text.segment";
  turn_id: string;
  voice_session_id: string;
  segment_index: number;
  text: string;
}

export interface VoiceAssistantTurnAudioChunkEvent {
  type: "audio.chunk";
  turn_id: string;
  voice_session_id: string;
  segment_index: number;
  sequence: number;
  audio: string;
  mime_type: string;
  provider?: string;
  model?: string;
  voice?: string;
  speed_ratio?: number;
  sample_rate?: number | null;
  channels?: number | null;
  bits_per_sample?: number | null;
}

export interface VoiceAssistantTurnAudioSegmentDoneEvent {
  type: "audio.segment_done";
  turn_id: string;
  voice_session_id: string;
  segment_index: number;
  duration_seconds?: number | null;
}

export interface VoiceAssistantTurnCompletedEvent {
  type: "turn.completed";
  turn_id: string;
  voice_session_id: string;
  state: "completed";
  message_id: string;
  audio_file_id?: string | null;
  text: string;
  primary_intent: string;
  intent_tags: string[];
  expects_answer: boolean;
  finish_reason?: string | null;
  usage?: Record<string, unknown>;
}

export interface VoiceAssistantTurnTerminalEvent {
  type: "turn.failed" | "turn.cancelled";
  turn_id?: string;
  voice_session_id?: string;
  state?: string;
  error?: string;
}

export interface PreparedAssistantStreamAudioSegment extends PreparedStreamingTTSAudioSegment {
  turnId: string;
}

export interface VoiceAssistantTurnStreamCallbacks {
  onStarted?(event: VoiceAssistantTurnStartedEvent): void;
  onTextDelta?(event: VoiceAssistantTurnTextDeltaEvent): void;
  onTextSegment?(event: VoiceAssistantTurnTextSegmentEvent): void;
  onAudioSegment?(segment: PreparedAssistantStreamAudioSegment): void;
  onCompleted?(event: VoiceAssistantTurnCompletedEvent): void;
  onCancelled?(event: VoiceAssistantTurnTerminalEvent): void;
  onFailed?(event: VoiceAssistantTurnTerminalEvent): void;
}

export class VoiceAssistantTurnStreamClient {
  private socketTask: WechatMiniprogram.SocketTask | null = null;
  private socketOpen = false;
  private closed = false;
  private startSent = false;
  private segmentAssembler: StreamingTTSSegmentAssembler | null = null;
  private messageChain: Promise<void> = Promise.resolve();
  private resolveStart?: (event: VoiceAssistantTurnCompletedEvent | VoiceAssistantTurnTerminalEvent) => void;
  private rejectStart?: (error: Error) => void;

  constructor(
    private readonly voiceSessionId: string,
    private readonly callbacks: VoiceAssistantTurnStreamCallbacks = {},
  ) {}

  async start(options: VoiceAssistantTurnStreamStartOptions = {}): Promise<VoiceAssistantTurnCompletedEvent> {
    this.closed = false;
    this.startSent = false;
    this.messageChain = Promise.resolve();
    this.segmentAssembler = null;
    const connectTimeoutMs = options.connectTimeoutMs ?? SOCKET_CONNECT_TIMEOUT_MS;
    const cookie = await backendSessionCookieHeader();
    const url = assistantTurnStreamUrl(this.voiceSessionId);
    const startPayload = this.startPayload(options);
    const terminalEvent = await new Promise<VoiceAssistantTurnCompletedEvent | VoiceAssistantTurnTerminalEvent>(
      (resolve, reject) => {
        this.resolveStart = resolve;
        this.rejectStart = reject;
        let connectTimer: ReturnType<typeof setTimeout> | null = setTimeout(() => {
          connectTimer = null;
          if (this.closed || this.socketOpen) {
            return;
          }
          this.closed = true;
          this.closeSocket("assistant turn stream timeout");
          reject(new Error("AI 回应连接超时"));
        }, connectTimeoutMs);
        const clearConnectTimer = () => {
          if (connectTimer !== null) {
            clearTimeout(connectTimer);
            connectTimer = null;
          }
        };
        const settle = (
          callback: () => void,
        ) => {
          clearConnectTimer();
          callback();
        };
        const socketTask = wx.connectSocket({
          url,
          header: cookie ? { Cookie: cookie } : {},
          fail: (error) => {
            settle(() => reject(new Error(error.errMsg || "AI 回应连接失败")));
          },
        });
        this.socketTask = socketTask;
        socketTask.onOpen(() => {
          if (this.closed) {
            socketTask.close({ code: 1000, reason: "assistant turn stream closed" });
            return;
          }
          clearConnectTimer();
          this.socketOpen = true;
        });
        socketTask.onMessage((message) => {
          if (!this.closed) {
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
                settle(() => reject(error instanceof Error ? error : new Error("AI 回应处理失败")));
                this.closeSocket("assistant turn stream failed");
              });
          }
        });
        socketTask.onError((error) => {
          if (this.closed) {
            return;
          }
          settle(() => reject(new Error(error.errMsg || "AI 回应连接失败")));
        });
        socketTask.onClose(() => {
          this.socketOpen = false;
          if (!this.closed) {
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

  cancel(): void {
    if (this.closed) {
      return;
    }
    this.sendJson({ type: "turn.cancel" });
    this.resolveStart?.({
      type: "turn.cancelled",
      voice_session_id: this.voiceSessionId,
      state: "cancelled",
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

  disposeTempFiles(): void {
    this.segmentAssembler?.disposeTempFiles();
  }

  private startPayload(options: VoiceAssistantTurnStreamStartOptions): Record<string, unknown> {
    const ttsSettings = readChatTTSSettings();
    const speedRatio = options.speedRatio ?? ttsSettings.speedRatio;
    return {
      type: "turn.start",
      ai_model: options.aiModel || readSelectedAIModel(),
      regenerate: options.regenerate || false,
      voice: options.voice || ttsSettings.voice || undefined,
      speed_ratio: speedRatio === 1 ? undefined : speedRatio,
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
    if (event.type === "text.segment") {
      this.rememberSegmentText(event as unknown as VoiceAssistantTurnTextSegmentEvent);
      this.callbacks.onTextSegment?.(event as unknown as VoiceAssistantTurnTextSegmentEvent);
      return;
    }
    if (event.type === "audio.chunk") {
      this.appendAudioChunk(event as unknown as VoiceAssistantTurnAudioChunkEvent);
      return;
    }
    if (event.type === "audio.segment_done") {
      const segment = await this.finalizeAudioSegment(event as unknown as VoiceAssistantTurnAudioSegmentDoneEvent);
      if (segment) {
        this.callbacks.onAudioSegment?.(segment);
      }
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
    this.closed = true;
    this.socketTask?.close({ code: 1000, reason });
    this.socketTask = null;
    this.socketOpen = false;
  }

  private rememberSegmentText(event: VoiceAssistantTurnTextSegmentEvent): void {
    this.segmentAssemblerFor(event.turn_id).rememberText(event);
  }

  private appendAudioChunk(event: VoiceAssistantTurnAudioChunkEvent): void {
    this.segmentAssemblerFor(event.turn_id).appendChunk(event);
  }

  private async finalizeAudioSegment(
    event: VoiceAssistantTurnAudioSegmentDoneEvent,
  ): Promise<PreparedAssistantStreamAudioSegment | null> {
    const segment = await this.segmentAssemblerFor(event.turn_id).finalize(event);
    if (!segment) {
      return null;
    }
    return {
      ...segment,
      turnId: event.turn_id,
    };
  }

  private segmentAssemblerFor(turnId: string): StreamingTTSSegmentAssembler {
    if (!this.segmentAssembler) {
      this.segmentAssembler = new StreamingTTSSegmentAssembler(`assistant-turn-${turnId}`);
    }
    return this.segmentAssembler;
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
