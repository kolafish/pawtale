import {
  StreamingSpeechPlaybackController,
  type StreamingSpeechPlaybackFrameEvent,
  type StreamingSpeechPCMFrame,
} from "./streaming-speech-playback";
import {
  VoiceAssistantTurnStreamClient,
  type PreparedAssistantStreamAudioFrame,
  type VoiceAssistantTurnAudioDoneEvent,
  type VoiceAssistantTurnCompletedEvent,
  type VoiceAssistantTurnStartedEvent,
  type VoiceAssistantTurnStreamStartOptions,
  type VoiceAssistantTurnStreamCallbacks,
  type VoiceAssistantTurnTextDeltaEvent,
} from "./voice-assistant-turn-stream";

interface VoiceAssistantTurnStreamClientLike {
  start(options?: VoiceAssistantTurnStreamStartOptions): Promise<VoiceAssistantTurnCompletedEvent>;
  cancel(reason?: string): void;
  close(): void;
}

interface StreamingSpeechPlaybackControllerLike {
  appendFrame(frame: StreamingSpeechPCMFrame): Promise<StreamingSpeechPlaybackFrameEvent | null>;
  waitForDrain(): Promise<void>;
  stats(): ReturnType<StreamingSpeechPlaybackController["stats"]>;
  stop(): void;
  destroy(): Promise<void>;
}

export interface VoiceAssistantTurnRuntimeCallbacks {
  onStarted?(event: VoiceAssistantTurnStartedEvent): void;
  onTextDelta?(event: VoiceAssistantTurnTextDeltaEvent): void;
  onAudioFrame?(frame: PreparedAssistantStreamAudioFrame): void;
  onAudioFrameScheduled?(event: StreamingSpeechPlaybackFrameEvent): void;
  onAudioDrained?(event: VoiceAssistantTurnAudioDoneEvent): void;
  onCompleted?(event: VoiceAssistantTurnCompletedEvent): void;
  onPlaybackError?(error: Error): void;
}

export interface VoiceAssistantTurnRuntimeOptions {
  createStreamClient?: (
    voiceSessionId: string,
    callbacks: VoiceAssistantTurnStreamCallbacks,
  ) => VoiceAssistantTurnStreamClientLike;
  createPlaybackController?: () => StreamingSpeechPlaybackControllerLike;
  isActive?: () => boolean;
}

export interface VoiceAssistantTurnRuntimeResult {
  completed: VoiceAssistantTurnCompletedEvent;
  playbackError: Error | null;
}

export class VoiceAssistantTurnRuntime {
  private streamClient: VoiceAssistantTurnStreamClientLike | null = null;
  private playbackController: StreamingSpeechPlaybackControllerLike | null = null;
  private playbackChain: Promise<void> = Promise.resolve();
  private playbackError: Error | null = null;
  private active = false;

  constructor(
    private readonly voiceSessionId: string,
    private readonly callbacks: VoiceAssistantTurnRuntimeCallbacks = {},
    private readonly options: VoiceAssistantTurnRuntimeOptions = {},
  ) {}

  async start(options: VoiceAssistantTurnStreamStartOptions = {}): Promise<VoiceAssistantTurnRuntimeResult> {
    this.active = true;
    this.playbackChain = Promise.resolve();
    this.playbackError = null;
    const streamClient = this.createStreamClient();
    this.streamClient = streamClient;
    try {
      const completed = await streamClient.start(options);
      if (!this.active) {
        throw new Error("AI 回应已取消");
      }
      await this.playbackChain;
      const playbackError = this.playbackError;
      await this.releasePlaybackController();
      this.active = false;
      this.streamClient = null;
      return { completed, playbackError };
    } catch (error) {
      this.active = false;
      this.streamClient = null;
      this.stopPlayback();
      await this.releasePlaybackController();
      throw error;
    }
  }

  cancel(reason = "cancelled"): void {
    if (!this.active && !this.streamClient) {
      return;
    }
    this.active = false;
    this.streamClient?.cancel(reason);
    this.streamClient = null;
    this.resetPlayback();
  }

  interrupt(): void {
    this.cancel("interrupted_by_user");
  }

  close(): void {
    this.active = false;
    this.streamClient?.close();
    this.streamClient = null;
    this.resetPlayback();
  }

  stopPlayback(): void {
    this.playbackController?.stop();
  }

  stats(): ReturnType<StreamingSpeechPlaybackController["stats"]> | null {
    return this.playbackController?.stats() ?? null;
  }

  private createStreamClient(): VoiceAssistantTurnStreamClientLike {
    return (this.options.createStreamClient || ((voiceSessionId, callbacks) => (
      new VoiceAssistantTurnStreamClient(voiceSessionId, callbacks)
    )))(this.voiceSessionId, {
      onStarted: (event) => {
        if (this.callbacksActive()) {
          this.callbacks.onStarted?.(event);
        }
      },
      onTextDelta: (event) => {
        if (this.callbacksActive()) {
          this.callbacks.onTextDelta?.(event);
        }
      },
      onAudioFrame: (frame) => {
        if (this.callbacksActive()) {
          this.enqueueAudioFrame(frame);
        }
      },
      onAudioDone: (event) => {
        if (this.callbacksActive()) {
          this.finishAudioFrames(event);
        }
      },
      onCompleted: (event) => {
        if (this.callbacksActive()) {
          this.callbacks.onCompleted?.(event);
        }
      },
    });
  }

  private enqueueAudioFrame(frame: PreparedAssistantStreamAudioFrame): void {
    this.playbackChain = this.playbackChain
      .then(() => this.playAudioFrame(frame))
      .catch((error) => this.recordPlaybackError(error));
  }

  private finishAudioFrames(event: VoiceAssistantTurnAudioDoneEvent): void {
    this.playbackChain = this.playbackChain
      .then(async () => {
        if (this.playbackError) {
          return;
        }
        const controller = this.playbackController;
        if (controller) {
          await controller.waitForDrain();
        }
        if (this.callbacksActive() && !this.playbackError) {
          this.callbacks.onAudioDrained?.(event);
        }
      })
      .catch((error) => this.recordPlaybackError(error));
  }

  private async playAudioFrame(frame: PreparedAssistantStreamAudioFrame): Promise<void> {
    if (!this.callbacksActive()) {
      return;
    }
    this.callbacks.onAudioFrame?.(frame);
    const scheduled = await this.playback().appendFrame(frame);
    if (!this.callbacksActive() || !scheduled) {
      return;
    }
    this.callbacks.onAudioFrameScheduled?.(scheduled);
  }

  private recordPlaybackError(error: unknown): void {
    if (!this.callbacksActive()) {
      return;
    }
    this.playbackError = error instanceof Error ? error : new Error("小余语音播放失败");
    this.callbacks.onPlaybackError?.(this.playbackError);
  }

  private callbacksActive(): boolean { return this.active && (this.options.isActive?.() ?? true); }

  private playback(): StreamingSpeechPlaybackControllerLike {
    if (!this.playbackController) {
      this.playbackController = (this.options.createPlaybackController || (() => (
        new StreamingSpeechPlaybackController()
      )))();
    }
    return this.playbackController;
  }

  private resetPlayback(): void {
    this.stopPlayback();
    this.playbackChain = Promise.resolve();
    this.playbackError = null;
    void this.releasePlaybackControllerSafely();
  }

  private async releasePlaybackController(): Promise<void> {
    const controller = this.playbackController;
    this.playbackController = null;
    if (controller) {
      await controller.destroy();
    }
  }

  private async releasePlaybackControllerSafely(): Promise<void> {
    try {
      await this.releasePlaybackController();
    } catch {
      // Cancellation already moved ownership away from the active turn.
    }
  }
}
