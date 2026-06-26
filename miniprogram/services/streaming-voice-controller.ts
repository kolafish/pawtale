import {
  MemoirStreamingAsrClient,
  type MemoirStreamingAsrCallbacks,
  type MemoirStreamingAsrOptions,
  type MemoirStartRecordingOptions,
} from "./memoir-streaming-asr";
import { sharedRecorderResource } from "./recorder-resource";

export type StreamingVoiceClientFactory = (
  options: MemoirStreamingAsrOptions,
) => MemoirStreamingAsrClient;

export class StreamingVoiceController {
  private client: MemoirStreamingAsrClient | null = null;

  constructor(
    private readonly createClient: StreamingVoiceClientFactory = (
      options,
    ) => new MemoirStreamingAsrClient(options, sharedRecorderResource),
  ) {}

  hasClient(): boolean {
    return Boolean(this.client);
  }

  currentClient(): MemoirStreamingAsrClient | null {
    return this.client;
  }

  async connect(options: MemoirStreamingAsrOptions): Promise<MemoirStreamingAsrClient> {
    this.close({ force: true });
    let client: MemoirStreamingAsrClient | null = null;
    client = this.createClient({
      ...options,
      callbacks: this.currentCallbacks(() => client, options.callbacks),
    });
    this.client = client;
    try {
      await client.connect();
      return client;
    } catch (error) {
      if (this.client === client) {
        this.client = null;
      }
      client.close({ force: true });
      throw error;
    }
  }

  startRecording(options: MemoirStartRecordingOptions = {}): boolean {
    return this.client?.startRecording(options) || false;
  }

  stopRecording(): void {
    this.client?.stopRecording();
  }

  configureAutoStop(autoStopSilenceMs: number): void {
    this.client?.configureAutoStop(autoStopSilenceMs);
  }

  async finishSession(): Promise<boolean> {
    const client = this.client;
    if (!client) {
      return false;
    }
    await client.finishSession();
    if (this.client === client) {
      this.client = null;
    }
    return true;
  }

  close(options?: { force?: boolean }): void {
    this.client?.close(options);
    this.client = null;
  }

  private currentCallbacks(
    clientRef: () => MemoirStreamingAsrClient | null,
    callbacks: MemoirStreamingAsrCallbacks,
  ): MemoirStreamingAsrCallbacks {
    const isCurrent = () => {
      const client = clientRef();
      return Boolean(client && this.client === client);
    };
    return {
      onStatus: (status) => {
        if (isCurrent()) {
          callbacks.onStatus(status);
        }
      },
      onCaptureStats: (stats) => {
        if (isCurrent()) {
          callbacks.onCaptureStats?.(stats);
        }
      },
      onEvent: (event) => {
        if (isCurrent()) {
          callbacks.onEvent(event);
        }
      },
      onPartial: (text) => {
        if (isCurrent()) {
          callbacks.onPartial(text);
        }
      },
      onFinal: (event) => {
        if (isCurrent()) {
          callbacks.onFinal(event);
        }
      },
      onError: (message, error) => {
        if (isCurrent()) {
          callbacks.onError(message, error);
        }
      },
    };
  }
}
