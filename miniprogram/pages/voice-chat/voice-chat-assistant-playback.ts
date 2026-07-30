import type { TTSAlignmentSegment } from "../../domain/memoir-audio";
import type { MemoirSpeakerRole } from "../../domain/memoir-voice-session";
import { AudioPlaybackController } from "../../services/audio-playback";
import {
  applyAssistantTTSResponse,
  type AssistantTurnQueueItem,
  type PreparedAssistantTurnAudio,
} from "../../services/voice-assistant-turn-queue";
import { VoiceAssistantTurnRuntime } from "../../services/voice-assistant-turn-runtime";
import {
  streamingProgressPercent,
  VoiceAssistantTTSProgressRuntime,
  type VoiceAssistantTTSProgressPatch,
} from "../../services/voice-assistant-tts-progress";
import { VoiceBargeInMonitor } from "../../services/voice-barge-in-monitor";
import { createMemoirAssistantTTS } from "../../services/memoir-voice-session-api";
import { readChatTTSSettings } from "../../services/tts-settings";
import type { StreamingSpeechPlaybackFrameEvent } from "../../services/streaming-speech-playback";
import {
  assistantTranscriptState,
  canRequestFollowUp,
  ttsAlignmentFromMetadata,
  upsertTranscriptLine,
} from "../../services/voice-chat-state";
import { writeMemoirVoiceSessionSnapshot } from "../../stores/memoir-cache";
import {
  applyVoiceChatDataPatch,
  bookCompletionRoundFinished,
  type VoiceChatData,
  type VoiceChatDataContext,
} from "./voice-chat-page-state";

interface PlayAudioFileOptions {
  autoListenAfterPlayback?: boolean;
  text?: string;
  alignment?: TTSAlignmentSegment[];
  durationSeconds?: number;
}

export interface VoiceChatAssistantPlaybackMethods {
  enqueueAssistantTurn(item: AssistantTurnQueueItem): void;
  drainAssistantTurnQueue(): Promise<void>;
  prepareAssistantTurnAudio(item: AssistantTurnQueueItem): Promise<PreparedAssistantTurnAudio>;
  showAssistantTurn(item: AssistantTurnQueueItem, patch?: Partial<VoiceChatData>): void;
  playPreparedAssistantTurn(item: AssistantTurnQueueItem, prepared: PreparedAssistantTurnAudio): Promise<void>;
  prepareFollowUpTTS(messageId: string, forceRegenerate?: boolean, autoListenAfterPlayback?: boolean): Promise<void>;
  playFollowUpAudio(): Promise<void>;
  playAudioFile(audioFileId: string, options?: PlayAudioFileOptions): Promise<void>;
  stopFollowUpAudio(): void;
  cancelFollowUpTTS(reason?: string): void;
  assistantTurnActiveForBargeIn(): boolean;
  startAssistantBargeInMonitor(requestSerial: number): void;
  stopAssistantBargeInMonitor(): Promise<void>;
  handleAssistantBargeIn(requestSerial: number): Promise<void>;
  markStreamingAudioFrameScheduled(event: StreamingSpeechPlaybackFrameEvent): void;
  updateStreamingTTSProgress(): void;
  updateFollowUpTTSProgress(currentTime: number, duration: number, fallbackDuration?: number): void;
  playbackTimeForProgress(audio: WechatMiniprogram.InnerAudioContext): number;
  streamingProgressPercent(spokenText: string, fullText: string, allowComplete: boolean): number;
  startStreamingProgressTimer(playToken: number): void;
  startFollowUpProgressTimer(
    playToken: number,
    audio: WechatMiniprogram.InnerAudioContext,
    fallbackDuration?: number,
  ): void;
  stopFollowUpProgressTimer(): void;
  resetFollowUpTTSProgress(): void;
  applyTTSProgressPatch(patch: VoiceAssistantTTSProgressPatch): void;
}

export interface VoiceChatAssistantPlaybackHost extends VoiceChatDataContext {
  assistantTurnQueue: AssistantTurnQueueItem[];
  assistantTurnQueueRunning: boolean;
  assistantAudioPrepareCount: number;
  assistantStreamLineId: string;
  assistantTurnRuntime: VoiceAssistantTurnRuntime | null;
  assistantBargeInMonitor: VoiceBargeInMonitor;
  audioPlayback: AudioPlaybackController;
  ttsPlayToken: number;
  ttsProgress: VoiceAssistantTTSProgressRuntime;
  ttsRequestSerial: number;
  followUpRequestSerial: number;
  autoListenPendingAfterPlayback: boolean;
  ensureVoiceAuthorization(autoTriggered?: boolean): boolean;
  startRecordingAfterPrivacy(
    autoTriggered?: boolean,
    speakerRole?: MemoirSpeakerRole,
    requestedGeneration?: number,
  ): Promise<boolean>;
  tryStartAutoListenAfterTTS(): Promise<boolean>;
}

type VoiceChatAssistantPlaybackContext = VoiceChatAssistantPlaybackHost & VoiceChatAssistantPlaybackMethods;

export const voiceChatAssistantPlaybackMethods: VoiceChatAssistantPlaybackMethods
  & ThisType<VoiceChatAssistantPlaybackContext> = {
  enqueueAssistantTurn(item: AssistantTurnQueueItem) {
    if (!item.messageId) {
      return;
    }
    const transcriptLines = upsertTranscriptLine(this.data.transcriptLines, item.line);
    applyVoiceChatDataPatch(this, {
      transcriptLines,
    });
    writeMemoirVoiceSessionSnapshot(this.data.voiceSessionId, { transcriptLines });
    item.prepare = this.prepareAssistantTurnAudio(item);
    this.assistantTurnQueue.push(item);
    if (!this.assistantTurnQueueRunning) {
      void this.drainAssistantTurnQueue();
    }
  },

  async drainAssistantTurnQueue() {
    if (this.assistantTurnQueueRunning) {
      return;
    }
    this.assistantTurnQueueRunning = true;
    try {
      while (this.assistantTurnQueue.length) {
        const item = this.assistantTurnQueue.shift();
        if (!item) {
          continue;
        }
        this.showAssistantTurn(item, {
          followUpTTSStateText: item.prepare ? "正在准备语音" : this.data.followUpTTSStateText,
        });
        try {
          const prepared = await item.prepare;
          if (!prepared) {
            continue;
          }
          await this.playPreparedAssistantTurn(item, prepared);
        } catch (error) {
          if (error instanceof Error && error.message === "__assistant_audio_cancelled__") {
            continue;
          }
          applyVoiceChatDataPatch(this, {
            followUpTTSError: error instanceof Error ? error.message : "小余语音生成失败",
            followUpTTSStateText: "语音暂不可用",
          });
        }
      }
    } finally {
      this.assistantTurnQueueRunning = false;
    }
  },

  async prepareAssistantTurnAudio(item: AssistantTurnQueueItem) {
    if (!this.data.voiceSessionId || !item.messageId) {
      throw new Error("缺少要合成的小余回应");
    }
    const requestSerial = this.ttsRequestSerial;
    this.assistantAudioPrepareCount += 1;
    applyVoiceChatDataPatch(this, {
      followUpTTSLoading: true,
      followUpTTSError: "",
      followUpTTSStateText: "正在合成语音",
    });
    try {
      const ttsSettings = readChatTTSSettings();
      const response = await createMemoirAssistantTTS(this.data.voiceSessionId, item.messageId, {
        voice: ttsSettings.voice || undefined,
        speed_ratio: ttsSettings.speedRatio === 1 ? undefined : ttsSettings.speedRatio,
        force_regenerate: item.forceRegenerate,
      });
      if (requestSerial !== this.ttsRequestSerial) {
        throw new Error("__assistant_audio_cancelled__");
      }
      const { lines, prepared } = applyAssistantTTSResponse(this.data.transcriptLines, response);
      applyVoiceChatDataPatch(this, {
        transcriptLines: lines,
        followUpTTSError: "",
        followUpTTSStateText: this.data.followUpTTSPlaying ? "下一句语音已准备" : "语音已生成",
      });
      writeMemoirVoiceSessionSnapshot(this.data.voiceSessionId, { transcriptLines: lines });
      return prepared;
    } catch (error) {
      throw error instanceof Error ? error : new Error("小余语音生成失败");
    } finally {
      this.assistantAudioPrepareCount = Math.max(0, this.assistantAudioPrepareCount - 1);
      if (this.assistantAudioPrepareCount === 0) {
        applyVoiceChatDataPatch(this, { followUpTTSLoading: false });
      }
    }
  },

  showAssistantTurn(item: AssistantTurnQueueItem, patch: Partial<VoiceChatData> = {}) {
    const transcriptLines = upsertTranscriptLine(this.data.transcriptLines, item.line);
    applyVoiceChatDataPatch(this, {
      ...assistantTranscriptState(transcriptLines, {
        guidanceMode: this.data.guidanceMode,
        lastFollowUpSourceMessageId: this.data.lastFollowUpSourceMessageId,
        followUpLoading: this.data.followUpLoading,
        assistantPromptText: item.text,
        followUpMessageId: item.messageId,
        followUpAudioFileId: item.line.audio_file_id || "",
        followUpTTSStateText: this.data.followUpTTSStateText,
        ttsAlignment: ttsAlignmentFromMetadata(item.line.metadata_json),
        assistantExpectsAnswer: item.expectsAnswer,
        canRequestFollowUp: false,
      }),
      ...patch,
    });
  },

  async playPreparedAssistantTurn(item: AssistantTurnQueueItem, prepared: PreparedAssistantTurnAudio) {
    this.showAssistantTurn({
      ...item,
      line: {
        ...item.line,
        audio_file_id: prepared.audioFileId,
        metadata_json: prepared.metadata || item.line.metadata_json,
      },
    }, {
      followUpAudioFileId: prepared.audioFileId,
      ttsAlignment: prepared.alignment,
      followUpTTSStateText: "语音已生成",
    });
    await this.playAudioFile(prepared.audioFileId, {
      autoListenAfterPlayback: item.autoListenAfterPlayback,
      text: item.text,
      alignment: prepared.alignment,
    });
  },

  async prepareFollowUpTTS(messageId: string, forceRegenerate: boolean = false, autoListenAfterPlayback: boolean = true) {
    if (!this.data.voiceSessionId || !messageId) {
      return;
    }
    const line = this.data.transcriptLines.find((item) => (item.message_id || item.id) === messageId);
    if (!line) {
      return;
    }
    this.enqueueAssistantTurn({
      messageId,
      text: line.text,
      expectsAnswer: line.metadata_json?.expects_answer !== false,
      autoListenAfterPlayback,
      forceRegenerate,
      line,
    });
  },

  async playFollowUpAudio() {
    if (this.data.followUpTTSLoading) {
      return;
    }
    if (this.data.followUpAudioFileId) {
      await this.playAudioFile(this.data.followUpAudioFileId, {
        autoListenAfterPlayback: false,
        text: this.data.assistantPromptText,
        alignment: this.data.ttsAlignment,
        durationSeconds: this.ttsProgress.streamingDurationSeconds(),
      });
      return;
    }
    if (this.data.followUpMessageId) {
      await this.prepareFollowUpTTS(this.data.followUpMessageId, false, false);
    }
  },

  async playAudioFile(audioFileId: string, options: {
      autoListenAfterPlayback?: boolean;
      text?: string;
      alignment?: TTSAlignmentSegment[];
      durationSeconds?: number;
    } = {}) {
    if (!audioFileId) {
      return;
    }
    this.stopFollowUpAudio();
    const playToken = this.audioPlayback.nextToken();
    this.ttsPlayToken = playToken;
    applyVoiceChatDataPatch(this, {
      assistantPromptText: options.text || this.data.assistantPromptText,
      ttsAlignment: options.alignment || this.data.ttsAlignment,
      followUpTTSPlaying: true,
      followUpTTSError: "",
      followUpTTSStateText: "小余正在读问题",
      ttsSpokenText: "",
      ttsCurrentText: "",
      ttsRemainingText: options.text || this.data.assistantPromptText,
      ttsProgressPercent: 0,
    });
    const fallbackDuration = Number.isFinite(options.durationSeconds) && Number(options.durationSeconds) > 0
      ? Number(options.durationSeconds)
      : 0;
    this.updateFollowUpTTSProgress(0, 0, fallbackDuration);
    try {
      await this.audioPlayback.playFile(audioFileId, playToken, {
        onPlay: (audio) => {
          if (playToken === this.ttsPlayToken) {
            this.ttsProgress.markPlaybackStarted();
            this.startFollowUpProgressTimer(playToken, audio, fallbackDuration);
            this.startAssistantBargeInMonitor(this.followUpRequestSerial);
          }
        },
        onTimeUpdate: (audio) => {
          if (playToken === this.ttsPlayToken) {
            this.updateFollowUpTTSProgress(
              this.playbackTimeForProgress(audio),
              audio.duration,
              fallbackDuration,
            );
          }
        },
        onPlayRequested: (audio) => {
          this.startFollowUpProgressTimer(playToken, audio, fallbackDuration);
        },
        onEnded: () => {
          if (playToken === this.ttsPlayToken) {
            this.stopAssistantBargeInMonitor();
            if (options.autoListenAfterPlayback) {
              this.autoListenPendingAfterPlayback = true;
            }
            this.stopFollowUpProgressTimer();
            applyVoiceChatDataPatch(this, {
              followUpTTSPlaying: false,
              followUpTTSStateText: options.autoListenAfterPlayback ? "播放完成，正在听你说" : "播放完成",
              ttsSpokenText: this.data.assistantPromptText,
              ttsCurrentText: "",
              ttsRemainingText: "",
              ttsProgressPercent: 100,
            });
          }
          if (playToken === this.ttsPlayToken && options.autoListenAfterPlayback) {
            void this.tryStartAutoListenAfterTTS();
          }
        },
        onError: () => {
          if (playToken === this.ttsPlayToken) {
            this.stopAssistantBargeInMonitor();
            this.stopFollowUpProgressTimer();
            applyVoiceChatDataPatch(this, {
              followUpTTSPlaying: false,
              followUpTTSError: "小余语音播放失败",
              followUpTTSStateText: "播放失败",
            });
          }
        },
        errorMessage: "小余语音播放失败",
      });
    } catch (error) {
      if (playToken === this.ttsPlayToken) {
        this.stopAssistantBargeInMonitor();
        applyVoiceChatDataPatch(this, {
          followUpTTSPlaying: false,
          followUpTTSError: error instanceof Error ? error.message : "小余语音播放失败",
          followUpTTSStateText: "播放失败",
        });
      }
    }
  },

  stopFollowUpAudio() {
    this.stopAssistantBargeInMonitor();
    this.audioPlayback.stop();
    this.assistantTurnRuntime?.stopPlayback();
    this.ttsPlayToken = this.audioPlayback.currentToken();
    this.autoListenPendingAfterPlayback = false;
    this.stopFollowUpProgressTimer();
    if (this.data.followUpTTSPlaying) {
      applyVoiceChatDataPatch(this, { followUpTTSPlaying: false });
    }
    this.resetFollowUpTTSProgress();
  },

  cancelFollowUpTTS(reason: string = "cancelled") {
    this.ttsRequestSerial += 1;
    const partialAssistantLineId = this.assistantStreamLineId;
    const transcriptLines = partialAssistantLineId
      ? this.data.transcriptLines.filter((line) => line.id !== partialAssistantLineId)
      : this.data.transcriptLines;
    this.assistantTurnRuntime?.cancel(reason);
    this.assistantTurnRuntime = null;
    this.assistantStreamLineId = "";
    this.ttsProgress.resetStreamingAudio();
    this.assistantTurnQueue = [];
    this.assistantAudioPrepareCount = 0;
    this.stopFollowUpAudio();
    if (partialAssistantLineId) {
      applyVoiceChatDataPatch(this, {
        ...assistantTranscriptState(transcriptLines, {
          guidanceMode: this.data.guidanceMode,
          lastFollowUpSourceMessageId: this.data.lastFollowUpSourceMessageId,
          followUpLoading: false,
          assistantPromptText: "",
          followUpMessageId: "",
          followUpAudioFileId: "",
          followUpTTSStateText: "",
          ttsAlignment: [],
          assistantExpectsAnswer: true,
          canRequestFollowUp: canRequestFollowUp(
            transcriptLines,
            this.data.guidanceMode,
            this.data.lastFollowUpSourceMessageId,
            false,
          ),
        }),
      });
    }
    if (this.data.followUpLoading || this.data.followUpTTSLoading || this.data.followUpTTSPlaying) {
      applyVoiceChatDataPatch(this, {
        followUpLoading: false,
        followUpTTSLoading: false,
        followUpTTSPlaying: false,
        followUpTTSStateText: this.data.followUpTTSStateText ? "语音已停止" : "",
      });
    }
  },

  assistantTurnActiveForBargeIn() {
    return Boolean(this.assistantTurnRuntime)
      || this.data.followUpLoading
      || this.data.followUpTTSLoading
      || this.data.followUpTTSPlaying;
  },

  startAssistantBargeInMonitor(requestSerial: number) {
    if (
      this.data.recording
      || bookCompletionRoundFinished(this.data)
      || !this.data.autoBargeInEnabled
      || !this.assistantTurnActiveForBargeIn()
      || this.assistantBargeInMonitor.isRunning()
    ) {
      if (!this.data.autoBargeInEnabled && this.assistantTurnActiveForBargeIn()) {
        applyVoiceChatDataPatch(this, { autoListenStateText: "可以点按钮回答" });
      }
      return;
    }
    const started = this.assistantBargeInMonitor.start({
      onBargeIn: () => {
        void this.handleAssistantBargeIn(requestSerial);
      },
      onError: () => {
        if (
          requestSerial === this.followUpRequestSerial
          && !this.data.recording
          && this.assistantTurnActiveForBargeIn()
        ) {
          applyVoiceChatDataPatch(this, { autoListenStateText: "可以点按钮回答" });
        }
      },
    });
    if (started) {
      applyVoiceChatDataPatch(this, { autoListenStateText: "想说时可以直接开口" });
    }
  },

  stopAssistantBargeInMonitor() {
    return this.assistantBargeInMonitor.stopAndWait();
  },

  async handleAssistantBargeIn(requestSerial: number) {
    if (
      requestSerial !== this.followUpRequestSerial
      || this.data.recording
      || bookCompletionRoundFinished(this.data)
      || !this.assistantTurnActiveForBargeIn()
    ) {
      await this.stopAssistantBargeInMonitor();
      return;
    }
    applyVoiceChatDataPatch(this, {
      autoListenStateText: "听到您说话了",
    });
    if (!this.ensureVoiceAuthorization(false)) {
      await this.stopAssistantBargeInMonitor();
      return;
    }
    await this.startRecordingAfterPrivacy(false);
  },

  markStreamingAudioFrameScheduled(event: StreamingSpeechPlaybackFrameEvent) {
    this.ttsProgress.markStreamingAudioFrameScheduled(event);
  },

  updateStreamingTTSProgress() {
    this.ttsProgress.updateStreamingProgress(
      this.data.assistantPromptText,
      this.assistantTurnRuntime?.stats()?.currentTimeSeconds,
      (patch) => this.applyTTSProgressPatch(patch),
    );
  },

  updateFollowUpTTSProgress(currentTime: number, duration: number, fallbackDuration: number = 0) {
    this.ttsProgress.updateAudioFileProgress(
      this.data.assistantPromptText,
      currentTime,
      duration,
      fallbackDuration,
      this.data.ttsAlignment,
      (patch) => this.applyTTSProgressPatch(patch),
    );
  },

  playbackTimeForProgress(audio: WechatMiniprogram.InnerAudioContext) {
    return this.ttsProgress.playbackTimeForAudio(audio);
  },

  streamingProgressPercent(spokenText: string, fullText: string, allowComplete: boolean) {
    return streamingProgressPercent(spokenText, fullText, allowComplete);
  },

  startStreamingProgressTimer(playToken: number) {
    if (playToken !== this.ttsPlayToken) {
      return;
    }
    this.ttsProgress.startStreamingTimer({
      isActive: () => (
        playToken === this.ttsPlayToken
        && Boolean(this.assistantTurnRuntime)
        && this.data.followUpTTSPlaying
      ),
      currentTimeSeconds: () => this.assistantTurnRuntime?.stats()?.currentTimeSeconds,
      text: () => this.data.assistantPromptText,
      applyPatch: (patch) => this.applyTTSProgressPatch(patch),
    });
  },

  startFollowUpProgressTimer(playToken: number, audio: WechatMiniprogram.InnerAudioContext, fallbackDuration: number = 0) {
    this.ttsProgress.startAudioFileTimer({
      audio,
      isActive: () => (
        playToken === this.ttsPlayToken
        && this.audioPlayback.currentAudio() === audio
        && this.data.followUpTTSPlaying
      ),
      text: () => this.data.assistantPromptText,
      alignment: () => this.data.ttsAlignment,
      fallbackDurationSeconds: fallbackDuration,
      applyPatch: (patch) => this.applyTTSProgressPatch(patch),
    });
  },

  stopFollowUpProgressTimer() {
    this.ttsProgress.stopTimer();
  },

  resetFollowUpTTSProgress() {
    this.ttsProgress.resetProgress((patch) => this.applyTTSProgressPatch(patch));
  },

  applyTTSProgressPatch(patch: VoiceAssistantTTSProgressPatch) {
    applyVoiceChatDataPatch(this, patch);
  },
};
