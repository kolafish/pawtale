import { downloadMemoirVoiceAudio } from "./memoir-voice-api";

export type DownloadAudioFile = (audioFileId: string) => Promise<string>;

export interface AudioPlaybackControllerOptions {
  downloadAudioFile?: DownloadAudioFile;
  createAudioContext?: () => WechatMiniprogram.InnerAudioContext;
  configureAudio?: (audio: WechatMiniprogram.InnerAudioContext) => void;
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

export class AudioPlaybackController {
  private activePlayback: ActivePlayback | null = null;

  private playToken = 0;

  constructor(private readonly options: AudioPlaybackControllerOptions = {}) {}

  nextToken(): number {
    this.playToken += 1;
    return this.playToken;
  }

  currentToken(): number {
    return this.playToken;
  }

  isCurrentToken(token: number): boolean {
    return token === this.playToken;
  }

  async playFile(audioFileId: string, token = this.playToken): Promise<void> {
    const tempFilePath = await this.downloadAudioFile(audioFileId);
    if (!this.isCurrentToken(token)) {
      return;
    }
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
      audio.onEnded(() => {
        this.resolvePlayback(playback);
      });
      audio.onStop(() => {
        this.resolvePlayback(playback);
      });
      audio.onError((error) => {
        this.rejectPlayback(playback, new Error(error.errMsg || "音频播放失败"));
      });
      audio.play();
    });
  }

  stop(): void {
    this.nextToken();
    this.stopActivePlayback();
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
