import type { VoiceSessionRuntime } from "./voice-session-runtime";
import type { TTSAlignmentSegment } from "../../domain/memoir-audio";
import type { MemoirSpeakerRole } from "../../domain/memoir-voice-session";
import {
  applyAssistantTTSResponse,
  type AssistantTurnQueueItem,
  type PreparedAssistantTurnAudio,
} from "../../services/voice-assistant-turn-queue";
import {
  streamingProgressPercent,
  type VoiceAssistantTTSProgressPatch,
} from "../../services/voice-assistant-tts-progress";
import { ensureRecorderPrivacyAuthorization } from "../../services/wechat-privacy";
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
  startAssistantBargeInMonitor(requestSerial: number): Promise<void>;
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
  runtime(): VoiceSessionRuntime;
  ensureVoiceAuthorization(autoTriggered?: boolean): boolean;
  startRecording(autoTriggered?: boolean, speakerRole?: MemoirSpeakerRole): boolean;
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
    this.runtime().assistantTurnQueue.push(item);
    if (!this.runtime().assistantTurnQueueRunning) {
      void this.drainAssistantTurnQueue();
    }
  },

  async drainAssistantTurnQueue() {
    if (this.runtime().assistantTurnQueueRunning) {
      return;
    }
    this.runtime().assistantTurnQueueRunning = true;
    try {
      while (this.runtime().assistantTurnQueue.length) {
        const item = this.runtime().assistantTurnQueue.shift();
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
      this.runtime().assistantTurnQueueRunning = false;
    }
  },

  async prepareAssistantTurnAudio(item: AssistantTurnQueueItem) {
    if (!this.data.voiceSessionId || !item.messageId) {
      throw new Error("缺少要合成的小余回应");
    }
    const requestSerial = this.runtime().ttsRequestSerial;
    this.runtime().assistantAudioPrepareCount += 1;
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
      if (requestSerial !== this.runtime().ttsRequestSerial) {
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
      this.runtime().assistantAudioPrepareCount = Math.max(0, this.runtime().assistantAudioPrepareCount - 1);
      if (this.runtime().assistantAudioPrepareCount === 0) {
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
      autoListenAfterPlayback: autoListenAfterPlayback && line.metadata_json?.expects_answer !== false,
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
        durationSeconds: this.runtime().ttsProgress.streamingDurationSeconds(),
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
    const playToken = this.runtime().audioPlayback.nextToken();
    this.runtime().ttsPlayToken = playToken;
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
      await this.runtime().audioPlayback.playFile(audioFileId, playToken, {
        onPlay: (audio) => {
          if (playToken === this.runtime().ttsPlayToken) {
            this.runtime().ttsProgress.markPlaybackStarted();
            this.startFollowUpProgressTimer(playToken, audio, fallbackDuration);
            void this.startAssistantBargeInMonitor(this.runtime().followUpRequestSerial);
          }
        },
        onTimeUpdate: (audio) => {
          if (playToken === this.runtime().ttsPlayToken) {
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
          if (playToken === this.runtime().ttsPlayToken) {
            this.stopAssistantBargeInMonitor();
            if (options.autoListenAfterPlayback) {
              this.runtime().autoListenPendingAfterPlayback = true;
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
          if (playToken === this.runtime().ttsPlayToken && options.autoListenAfterPlayback) {
            void this.tryStartAutoListenAfterTTS();
          }
        },
        onError: () => {
          if (playToken === this.runtime().ttsPlayToken) {
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
      if (playToken === this.runtime().ttsPlayToken) {
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
    this.runtime().audioPlayback.stop();
    this.runtime().assistantTurnRuntime?.stopPlayback();
    this.runtime().ttsPlayToken = this.runtime().audioPlayback.currentToken();
    this.runtime().autoListenPendingAfterPlayback = false;
    this.stopFollowUpProgressTimer();
    if (this.data.followUpTTSPlaying) {
      applyVoiceChatDataPatch(this, { followUpTTSPlaying: false });
    }
    this.resetFollowUpTTSProgress();
  },

  cancelFollowUpTTS(reason: string = "cancelled") {
    this.runtime().ttsRequestSerial += 1;
    const partialAssistantLineId = this.runtime().assistantStreamLineId;
    const transcriptLines = partialAssistantLineId
      ? this.data.transcriptLines.filter((line) => line.id !== partialAssistantLineId)
      : this.data.transcriptLines;
    this.runtime().assistantTurnRuntime?.cancel(reason);
    this.runtime().assistantTurnRuntime = null;
    this.runtime().assistantStreamLineId = "";
    this.runtime().ttsProgress.resetStreamingAudio();
    this.runtime().assistantTurnQueue = [];
    this.runtime().assistantAudioPrepareCount = 0;
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
    return Boolean(this.runtime().assistantTurnRuntime)
      || this.data.followUpLoading
      || this.data.followUpTTSLoading
      || this.data.followUpTTSPlaying;
  },

  async startAssistantBargeInMonitor(requestSerial: number) {
    if (
      this.data.recording
      || this.runtime().pageSuspended
      || this.runtime().disposed
      || !this.data.voiceAuthorizationAgreed
      || !this.data.autoBargeInEnabled
      || requestSerial !== this.runtime().followUpRequestSerial
      || bookCompletionRoundFinished(this.data)
      || !this.assistantTurnActiveForBargeIn()
      || this.runtime().voiceStreaming.isBargeInMonitoring()
    ) {
      if (!this.data.autoBargeInEnabled && this.assistantTurnActiveForBargeIn()) {
        applyVoiceChatDataPatch(this, { autoListenStateText: "可以点按钮回答" });
      }
      return;
    }
    if (!this.data.voiceAuthorizationAgreed) {
      return;
    }
    try {
      await ensureRecorderPrivacyAuthorization();
    } catch {
      applyVoiceChatDataPatch(this, { autoListenStateText: "可以点按钮回答" });
      return;
    }
    if (
      this.data.recording
      || this.runtime().pageSuspended
      || this.runtime().disposed
      || !this.data.voiceAuthorizationAgreed
      || !this.data.autoBargeInEnabled
      || requestSerial !== this.runtime().followUpRequestSerial
      || bookCompletionRoundFinished(this.data)
      || !this.assistantTurnActiveForBargeIn()
      || this.runtime().voiceStreaming.isBargeInMonitoring()
    ) {
      return;
    }
    const started = this.runtime().voiceStreaming.startBargeInMonitoring({
      onBargeIn: () => {
        void this.handleAssistantBargeIn(requestSerial);
      },
      onError: () => {
        if (
          requestSerial === this.runtime().followUpRequestSerial
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
    return this.runtime().voiceStreaming.stopBargeInMonitoring();
  },

  async handleAssistantBargeIn(requestSerial: number) {
    if (
      requestSerial !== this.runtime().followUpRequestSerial
      || this.data.recording
      || bookCompletionRoundFinished(this.data)
      || !this.assistantTurnActiveForBargeIn()
    ) {
      await this.runtime().voiceStreaming.stopBargeInMonitoring(true);
      return;
    }
    applyVoiceChatDataPatch(this, {
      autoListenStateText: "听到您说话了",
    });
    if (!this.ensureVoiceAuthorization(false)) {
      await this.runtime().voiceStreaming.stopBargeInMonitoring(true);
      return;
    }
    try {
      if (this.startRecording(false)) await this.runtime().recordingStartPromise;
    } finally {
      await this.runtime().voiceStreaming.stopBargeInMonitoring(true);
    }
  },

  markStreamingAudioFrameScheduled(event: StreamingSpeechPlaybackFrameEvent) {
    this.runtime().ttsProgress.markStreamingAudioFrameScheduled(event);
  },

  updateStreamingTTSProgress() {
    this.runtime().ttsProgress.updateStreamingProgress(
      this.data.assistantPromptText,
      this.runtime().assistantTurnRuntime?.stats()?.currentTimeSeconds,
      (patch) => this.applyTTSProgressPatch(patch),
    );
  },

  updateFollowUpTTSProgress(currentTime: number, duration: number, fallbackDuration: number = 0) {
    this.runtime().ttsProgress.updateAudioFileProgress(
      this.data.assistantPromptText,
      currentTime,
      duration,
      fallbackDuration,
      this.data.ttsAlignment,
      (patch) => this.applyTTSProgressPatch(patch),
    );
  },

  playbackTimeForProgress(audio: WechatMiniprogram.InnerAudioContext) {
    return this.runtime().ttsProgress.playbackTimeForAudio(audio);
  },

  streamingProgressPercent(spokenText: string, fullText: string, allowComplete: boolean) {
    return streamingProgressPercent(spokenText, fullText, allowComplete);
  },

  startStreamingProgressTimer(playToken: number) {
    if (playToken !== this.runtime().ttsPlayToken) {
      return;
    }
    this.runtime().ttsProgress.startStreamingTimer({
      isActive: () => (
        playToken === this.runtime().ttsPlayToken
        && Boolean(this.runtime().assistantTurnRuntime)
        && this.data.followUpTTSPlaying
      ),
      currentTimeSeconds: () => this.runtime().assistantTurnRuntime?.stats()?.currentTimeSeconds,
      text: () => this.data.assistantPromptText,
      applyPatch: (patch) => this.applyTTSProgressPatch(patch),
    });
  },

  startFollowUpProgressTimer(playToken: number, audio: WechatMiniprogram.InnerAudioContext, fallbackDuration: number = 0) {
    this.runtime().ttsProgress.startAudioFileTimer({
      audio,
      isActive: () => (
        playToken === this.runtime().ttsPlayToken
        && this.runtime().audioPlayback.currentAudio() === audio
        && this.data.followUpTTSPlaying
      ),
      text: () => this.data.assistantPromptText,
      alignment: () => this.data.ttsAlignment,
      fallbackDurationSeconds: fallbackDuration,
      applyPatch: (patch) => this.applyTTSProgressPatch(patch),
    });
  },

  stopFollowUpProgressTimer() {
    this.runtime().ttsProgress.stopTimer();
  },

  resetFollowUpTTSProgress() {
    this.runtime().ttsProgress.resetProgress((patch) => this.applyTTSProgressPatch(patch));
  },

  applyTTSProgressPatch(patch: VoiceAssistantTTSProgressPatch) {
    applyVoiceChatDataPatch(this, patch);
  },
};
