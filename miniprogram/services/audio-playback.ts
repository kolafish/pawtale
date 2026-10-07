import { downloadMemoirVoiceAudio } from "./memoir-audio-api";

export type DownloadAudioFile = (audioFileId: string) => Promise<string>;

export interface AudioPlaybackControllerOptions {
  downloadAudioFile?: DownloadAudioFile;
  createAudioContext?: () => WechatMiniprogram.InnerAudioContext;
  configureAudio?: (audio: WechatMiniprogram.InnerAudioContext) => void;
}

export interface AudioPlaybackError {
  errMsg?: string;
}

export interface AudioPlaybackPlayOptions {
  onPlay?: (audio: WechatMiniprogram.InnerAudioContext) => void;
  onPlayRequested?: (audio: WechatMiniprogram.InnerAudioContext) => void;
  onTimeUpdate?: (audio: WechatMiniprogram.InnerAudioContext) => void;
  onEnded?: (audio: WechatMiniprogram.InnerAudioContext) => void;
  onStop?: (audio: WechatMiniprogram.InnerAudioContext) => void;
  onError?: (
    error: AudioPlaybackError,
    audio: WechatMiniprogram.InnerAudioContext,
  ) => void;
  errorMessage?: string | ((error: AudioPlaybackError) => string);
}

export interface AudioFileSequenceOptions {
  token: number;
  isCurrent: (token: number) => boolean;
  playFile: (audioFileId: string, index: number) => Promise<void>;
}

interface ActivePlayback {
  audio: WechatMiniprogram.InnerAudioContext;
  resolve: () => void;
  reject: (error: Error) => void;
  settled: boolean;
}

export function configureAudiblePlayback(audio: WechatMiniprogram.InnerAudioContext): void {
  wx.setInnerAudioOption?.({ obeyMuteSwitch: false, mixWithOther: true });
  audio.obeyMuteSwitch = false;
  audio.volume = 1;
}

export async function playAudioFileSequence(
  audioFileIds: string[],
  options: AudioFileSequenceOptions,
): Promise<boolean> {
  if (!audioFileIds.length) {
    throw new Error("没有可播放的音频");
  }
  for (let index = 0; index < audioFileIds.length; index += 1) {
    if (!options.isCurrent(options.token)) {
      return false;
    }
    await options.playFile(audioFileIds[index], index);
  }
  return options.isCurrent(options.token);
}

export class AudioPlaybackController {
  private activePlayback: ActivePlayback | null = null;
  private prefetchedFiles = new Map<string, Promise<string>>();

  private playToken = 0;

  constructor(private readonly options: AudioPlaybackControllerOptions = {}) {}

  nextToken(): number {
    this.playToken += 1;
    return this.playToken;
  }

  currentToken(): number {
    return this.playToken;
  }

  currentAudio(): WechatMiniprogram.InnerAudioContext | null {
    return this.activePlayback?.audio || null;
  }

  isCurrentToken(token: number): boolean {
    return token === this.playToken;
  }

  async playFile(audioFileId: string, token = this.playToken, options: AudioPlaybackPlayOptions = {}): Promise<void> {
    if (!this.isCurrentToken(token)) return;
    const download = this.prefetchedFiles.get(audioFileId) || this.downloadAudioFile(audioFileId);
    const tempFilePath = await download;
    if (this.prefetchedFiles.get(audioFileId) === download) this.prefetchedFiles.delete(audioFileId);
    if (!this.isCurrentToken(token)) {
      return;
    }
    await this.playSource(tempFilePath, options);
  }

  async prefetchFile(audioFileId: string): Promise<void> {
    if (!this.prefetchedFiles.has(audioFileId)) {
      this.prefetchedFiles.set(audioFileId, this.downloadAudioFile(audioFileId));
    }
    await this.prefetchedFiles.get(audioFileId);
  }

  async playTempFile(
    tempFilePath: string,
    token = this.playToken,
    options: AudioPlaybackPlayOptions = {},
  ): Promise<void> {
    if (!this.isCurrentToken(token)) {
      return;
    }
    await this.playSource(tempFilePath, options);
  }

  private async playSource(
    tempFilePath: string,
    options: AudioPlaybackPlayOptions,
  ): Promise<void> {
    this.stopActivePlayback();
    await new Promise<void>((resolve, reject) => {
      const audio = this.createAudioContext();
      const playback: ActivePlayback = {
        audio,
        resolve,
        reject,
        settled: false,
      };
      this.activePlayback = playback;
      this.configureAudio(audio);
      audio.src = tempFilePath;
      audio.onPlay(() => {
        options.onPlay?.(audio);
      });
      audio.onTimeUpdate(() => {
        options.onTimeUpdate?.(audio);
      });
      audio.onEnded(() => {
        options.onEnded?.(audio);
        this.resolvePlayback(playback);
      });
      audio.onStop(() => {
        options.onStop?.(audio);
        this.resolvePlayback(playback);
      });
      audio.onError((error) => {
        options.onError?.(error, audio);
        this.rejectPlayback(playback, this.errorFromPlayback(error, options));
      });
      audio.play();
      options.onPlayRequested?.(audio);
    });
  }

  stop(): void {
    this.nextToken();
    this.stopActivePlayback();
    this.prefetchedFiles.clear();
  }

  destroy(): void {
    this.stop();
  }

  private downloadAudioFile(audioFileId: string): Promise<string> {
    return (this.options.downloadAudioFile || downloadMemoirVoiceAudio)(audioFileId);
  }

  private createAudioContext(): WechatMiniprogram.InnerAudioContext {
    return this.options.createAudioContext?.() || wx.createInnerAudioContext();
  }

  private configureAudio(audio: WechatMiniprogram.InnerAudioContext): void {
    (this.options.configureAudio || configureAudiblePlayback)(audio);
  }

  private errorFromPlayback(
    error: AudioPlaybackError,
    options: AudioPlaybackPlayOptions,
  ): Error {
    if (typeof options.errorMessage === "function") {
      return new Error(options.errorMessage(error));
    }
    if (options.errorMessage) {
      return new Error(options.errorMessage);
    }
    return new Error(error.errMsg || "音频播放失败");
  }

  private stopActivePlayback(): void {
    const playback = this.activePlayback;
    if (!playback) {
      return;
    }
    try {
      playback.audio.stop();
    } finally {
      this.resolvePlayback(playback);
    }
  }

  private resolvePlayback(playback: ActivePlayback): void {
    if (this.settlePlayback(playback)) {
      playback.resolve();
    }
  }

  private rejectPlayback(playback: ActivePlayback, error: Error): void {
    if (this.settlePlayback(playback)) {
      playback.reject(error);
    }
  }

  private settlePlayback(playback: ActivePlayback): boolean {
    if (playback.settled) {
      return false;
    }
    playback.settled = true;
    playback.audio.destroy();
    if (this.activePlayback === playback) {
      this.activePlayback = null;
    }
    return true;
  }
}
