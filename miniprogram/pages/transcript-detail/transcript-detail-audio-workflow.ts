import { AudioPlaybackController } from "../../services/audio-playback";
import { trackAuditAction } from "../../services/audit-tracker";
import {
  getMemoirVoiceTranscript,
  startMemoirMaterialOfflineASR,
} from "../../services/memoir-transcript-api";
import { PollingTimer } from "../../services/polling-timer";
import {
  answerAudioFallbackDuration,
  answerPlaybackPatch,
  confirmOfflineAsr,
  OFFLINE_ASR_MAX_POLL_ATTEMPTS,
  positiveSeconds,
  toViewItem,
  type TranscriptDetailAudioKind,
  type TranscriptDetailData,
} from "./transcript-detail-model";

interface TranscriptDetailAudioWorkflowHost {
  data: TranscriptDetailData;
  audioPlayback: AudioPlaybackController;
  offlineAsrPoller: PollingTimer;
  offlineAsrPollToken: number;
  offlineAsrPollAttempts: number;
  setData(patch: Partial<TranscriptDetailData>): void;
}

export interface TranscriptDetailAudioWorkflowMethods {
  stopOfflineAsrPolling(): void;
  startOfflineAsrPollingIfNeeded(): void;
  scheduleOfflineAsrPoll(token: number): void;
  pollOfflineAsr(token: number): Promise<void>;
  startOfflineAsr(): Promise<void>;
  playAnswerAudio(): Promise<void>;
  playQuestionAudio(): Promise<void>;
  playAudio(kind: TranscriptDetailAudioKind): Promise<void>;
  toggleAnswerPlaybackPause(): void;
  updateAnswerPlaybackProgress(
    currentSeconds: number,
    durationSeconds: number,
    statusText: string,
  ): void;
  stopPlayback(): void;
}

type TranscriptDetailAudioWorkflow = TranscriptDetailAudioWorkflowHost
  & TranscriptDetailAudioWorkflowMethods;

export const transcriptDetailAudioWorkflowMethods:
TranscriptDetailAudioWorkflowMethods & ThisType<TranscriptDetailAudioWorkflow> = {
  stopOfflineAsrPolling() {
    this.offlineAsrPollToken += 1;
    this.offlineAsrPollAttempts = 0;
    this.offlineAsrPoller.cancel();
  },

  startOfflineAsrPollingIfNeeded() {
    if (!this.data.item?.offlineAsrActive) {
      return;
    }
    this.stopOfflineAsrPolling();
    const token = this.offlineAsrPollToken;
    this.scheduleOfflineAsrPoll(token);
  },

  scheduleOfflineAsrPoll(token: number) {
    if (token !== this.offlineAsrPollToken || !this.data.item?.offlineAsrActive) {
      return;
    }
    if (this.offlineAsrPollAttempts >= OFFLINE_ASR_MAX_POLL_ATTEMPTS) {
      return;
    }
    this.offlineAsrPoller.schedule(() => {
      void this.pollOfflineAsr(token);
    });
  },

  async pollOfflineAsr(token: number) {
    const current = this.data.item;
    if (!current || token !== this.offlineAsrPollToken) {
      return;
    }
    this.offlineAsrPollAttempts += 1;
    try {
      const transcript = await getMemoirVoiceTranscript(current.id);
      if (token !== this.offlineAsrPollToken) {
        return;
      }
      const next = toViewItem(transcript, current);
      this.setData({ item: next });
      if (next.offline_asr.status === "succeeded") {
        this.stopOfflineAsrPolling();
        wx.showToast({ title: "识别已更新", icon: "success" });
        return;
      }
      if (next.offline_asr.status === "failed") {
        this.stopOfflineAsrPolling();
        return;
      }
      this.scheduleOfflineAsrPoll(token);
    } catch {
      if (token !== this.offlineAsrPollToken) {
        return;
      }
      this.scheduleOfflineAsrPoll(token);
    }
  },

  async startOfflineAsr() {
    const item = this.data.item;
    if (
      !item
      || !item.showOfflineAsr
      || !item.canStartOfflineAsr
      || item.isEditing
      || this.data.offlineAsrStarting
      || this.data.saving
      || this.data.deleting
      || this.data.convertingQuestionToAnswer
    ) {
      return;
    }
    if (!await confirmOfflineAsr(item)) {
      return;
    }
    this.stopPlayback();
    this.setData({ offlineAsrStarting: true, error: "" });
    try {
      const state = await startMemoirMaterialOfflineASR(item.id);
      const next = toViewItem({ ...item, offline_asr: state }, item);
      this.setData({ item: next });
      trackAuditAction(
        "transcript.detail_offline_asr_start",
        {
          dialect: state.dialect_label,
          dialect_enhancement_enabled: state.dialect_enhancement_enabled,
        },
        { projectId: item.project_id },
      );
      wx.showToast({ title: "已开始识别", icon: "success" });
      this.startOfflineAsrPollingIfNeeded();
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "离线识别启动失败" });
    } finally {
      this.setData({ offlineAsrStarting: false });
    }
  },

  async playAnswerAudio() {
    await this.playAudio("answer");
  },

  async playQuestionAudio() {
    await this.playAudio("question");
  },

  async playAudio(kind: TranscriptDetailAudioKind) {
    const item = this.data.item;
    const audioFileId = kind === "question" ? item?.question_audio_file_id : item?.audio_file_id;
    if (!audioFileId) {
      this.setData({
        error: kind === "question" ? "小余提问没有可播放的语音" : "这段讲述没有可播放的原音",
      });
      return;
    }
    if (this.data.playing && this.data.playingAudioKind === kind) {
      this.stopPlayback();
      return;
    }
    if (this.data.saving || this.data.deleting || this.data.convertingQuestionToAnswer) {
      return;
    }
    this.stopPlayback();
    const token = this.audioPlayback.nextToken();
    const fallbackDuration = kind === "answer" ? answerAudioFallbackDuration(item) : 0;
    this.setData({
      playing: true,
      playingAudioKind: kind,
      ...(kind === "answer"
        ? {
          answerPlaybackVisible: true,
          answerPlaybackPaused: false,
          answerPlaybackCanTogglePause: false,
          answerPlaybackCanStop: true,
          answerPlaybackStopText: "停止",
          ...answerPlaybackPatch(0, fallbackDuration, "正在准备原音"),
        }
        : {}),
      error: "",
    });
    try {
      await this.audioPlayback.playFile(audioFileId, token, kind === "answer"
        ? {
          onPlay: (audio) => {
            if (!this.audioPlayback.isCurrentToken(token) || this.audioPlayback.currentAudio() !== audio) {
              return;
            }
            this.setData({
              answerPlaybackPaused: false,
              answerPlaybackCanTogglePause: true,
              answerPlaybackCanStop: true,
              answerPlaybackStopText: "停止",
            });
            this.updateAnswerPlaybackProgress(audio.currentTime, audio.duration || fallbackDuration, "正在播放");
          },
          onTimeUpdate: (audio) => {
            if (!this.audioPlayback.isCurrentToken(token) || this.audioPlayback.currentAudio() !== audio) {
              return;
            }
            this.updateAnswerPlaybackProgress(audio.currentTime, audio.duration || fallbackDuration, "正在播放");
          },
          onEnded: (audio) => {
            if (!this.audioPlayback.isCurrentToken(token)) {
              return;
            }
            const duration = positiveSeconds(audio.duration) || fallbackDuration;
            this.setData({
              answerPlaybackPaused: false,
              answerPlaybackCanTogglePause: false,
              answerPlaybackCanStop: true,
              answerPlaybackStopText: "收起",
              ...answerPlaybackPatch(duration, duration, "播放完成"),
            });
          },
        }
        : undefined);
    } catch (error) {
      if (this.audioPlayback.isCurrentToken(token)) {
        this.setData({
          error: error instanceof Error ? error.message : "播放失败",
          ...(kind === "answer"
            ? {
              answerPlaybackPaused: false,
              answerPlaybackCanTogglePause: false,
              answerPlaybackCanStop: true,
              answerPlaybackStopText: "收起",
              answerPlaybackStatusText: "播放失败",
            }
            : {}),
        });
      }
    } finally {
      if (this.audioPlayback.isCurrentToken(token)) {
        this.setData({
          playing: false,
          playingAudioKind: "",
          ...(kind === "answer"
            ? {
              answerPlaybackPaused: false,
              answerPlaybackCanTogglePause: false,
            }
            : {}),
        });
      }
    }
  },

  toggleAnswerPlaybackPause() {
    if (!this.data.answerPlaybackCanTogglePause || this.data.playingAudioKind !== "answer") {
      return;
    }
    const audio = this.audioPlayback.currentAudio();
    if (!audio) {
      return;
    }
    if (this.data.answerPlaybackPaused) {
      audio.play();
      this.setData({
        answerPlaybackPaused: false,
        answerPlaybackStatusText: "正在播放",
      });
      return;
    }
    audio.pause();
    this.setData({
      answerPlaybackPaused: true,
      answerPlaybackStatusText: "已暂停",
    });
  },

  updateAnswerPlaybackProgress(currentSeconds: number, durationSeconds: number, statusText: string) {
    this.setData(answerPlaybackPatch(currentSeconds, durationSeconds, statusText));
  },

  stopPlayback() {
    this.audioPlayback.stop();
    if (this.data.playing || this.data.answerPlaybackVisible) {
      this.setData({
        playing: false,
        playingAudioKind: "",
        answerPlaybackVisible: false,
        answerPlaybackPaused: false,
        answerPlaybackStatusText: "",
        answerPlaybackCanTogglePause: false,
        answerPlaybackCanStop: false,
        answerPlaybackStopText: "停止",
      });
    }
  },
};
