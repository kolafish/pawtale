import type { MemoirStoryDetail } from "../../domain/memoir";
import {
  createTextAnswer,
  getMemoirStoryDetail,
  updateTextAnswer,
} from "../../services/memoir";
import {
  clearMemoirAudioRecordingSession,
  durationText,
  type MemoirAudioRecorderSession,
  startMemoirAudioRecording,
  stopMemoirAudioRecording,
} from "../../services/memoir-audio-recorder";
import {
  markMemoirDashboardSnapshotStale,
  markMemoirStoriesSnapshotStale,
  readMemoirStoryDetailSnapshot,
  shouldRefreshMemoirStoryDetailSnapshot,
  type MemoirStoryDetailSnapshot,
  updateMemoirCachedStoryAnswer,
  writeMemoirStoryDetailSnapshot,
} from "../../stores/memoir-cache";
import {
  clearMemoirQaDetailInputDraft,
  readMemoirQaDetailInputDraft,
  writeMemoirQaDetailInputDraft,
} from "../../stores/memoir-input-drafts";

interface InputEvent {
  detail: {
    value: string;
  };
}

interface QaDetailData {
  answerId: string;
  questionId: string;
  loading: boolean;
  saving: boolean;
  submitting: boolean;
  recording: boolean;
  uploadingAudio: boolean;
  transcribingAudio: boolean;
  playing: boolean;
  error: string;
  sessionTitle: string;
  questionPrompt: string;
  transcriptRaw: string;
  answerText: string;
  hasAudio: boolean;
  audioLabel: string;
  audioTempUrl: string;
  audioError: string;
  newAudioFileId: string;
  newAudioDurationSeconds: number | null;
  newAudioSummary: string;
  inputDraftDirty: boolean;
}

interface QaDetailMethods {
  audioRecordingSession?: MemoirAudioRecorderSession;
  onLoad(query?: Record<string, string | undefined>): Promise<void>;
  onHide(): void;
  onUnload(): void;
  hydrateFromCache(): Promise<MemoirStoryDetailSnapshot>;
  applyInputDraft(answerId: string): boolean;
  persistInputDraft(): void;
  loadDetail(): Promise<void>;
  applyDetail(detail: MemoirStoryDetail, audioTempUrl: string, audioError: string): void;
  onAnswerInput(event: InputEvent): void;
  saveText(): Promise<void>;
  submitReanswer(): Promise<void>;
  startRecording(): void;
  stopRecording(): void;
  clearRecordedAudio(): void;
  playAudio(): void;
  stopAudio(): void;
  backQa(): void;
}

type QaDetailPageInstance = {
  data: QaDetailData;
  setData(data: Partial<QaDetailData>): void;
  persistInputDraft(): void;
  audioRecordingSession?: MemoirAudioRecorderSession;
};
let audioContext: WechatMiniprogram.InnerAudioContext | null = null;

function audioLabel(hasAudio: boolean, seconds?: number | null): string {
  if (!hasAudio) {
    return "未保存录音";
  }
  const text = durationText(seconds);
  return text ? `已保存录音 ${text}` : "已保存录音";
}

function isAudioBusy(data: Pick<QaDetailData, "recording" | "uploadingAudio" | "transcribingAudio">): boolean {
  return data.recording || data.uploadingAudio || data.transcribingAudio;
}

function inputDraftFromData(data: QaDetailData): Parameters<typeof writeMemoirQaDetailInputDraft>[0] | null {
  if (!data.answerId) {
    return null;
  }
  return {
    answer_id: data.answerId,
    question_id: data.questionId || undefined,
    answer_text: data.answerText,
    new_audio_file_id: data.newAudioFileId || undefined,
    new_audio_duration_seconds: data.newAudioDurationSeconds,
    new_audio_summary: data.newAudioSummary || undefined,
  };
}

function getTempFileUrl(fileId: string): Promise<string> {
  return new Promise((resolve, reject) => {
    wx.cloud.getTempFileURL({
      fileList: [fileId],
      success: (result) => {
        const file = result.fileList[0];
        if (file?.tempFileURL) {
          resolve(file.tempFileURL);
          return;
        }
        reject(new Error("录音播放地址生成失败"));
      },
      fail: (error) => {
        reject(new Error(error.errMsg || "录音播放地址生成失败"));
      },
    });
  });
}

function ensureAudioContext(page: { setData(data: Partial<QaDetailData>): void }): WechatMiniprogram.InnerAudioContext {
  if (audioContext) {
    return audioContext;
  }
  audioContext = wx.createInnerAudioContext();
  audioContext.onEnded(() => {
    page.setData({ playing: false });
  });
  audioContext.onError((error) => {
    page.setData({ playing: false, audioError: error.errMsg || "录音播放失败" });
  });
  return audioContext;
}

function createQaDetailRecorderSession(page: QaDetailPageInstance): MemoirAudioRecorderSession {
  return {
    onStart() {
      page.setData({ recording: true, error: "" });
    },
    onStop() {
      page.setData({ recording: false });
    },
    onMissingFile() {
      page.setData({ error: "录音保存失败" });
    },
    onUploadStart() {
      page.setData({ uploadingAudio: true, error: "", newAudioSummary: "正在保存录音" });
    },
    onUploaded(result) {
      page.setData({
        newAudioFileId: result.audioFile.id,
        newAudioDurationSeconds: result.durationSeconds,
        uploadingAudio: false,
        transcribingAudio: true,
        newAudioSummary: "正在转写",
        inputDraftDirty: true,
      });
      page.persistInputDraft();
    },
    onTranscribed(result) {
      page.setData({
        answerText: result.transcription.text,
        newAudioSummary: `已转写 ${result.durationText}`,
        inputDraftDirty: true,
      });
      page.persistInputDraft();
    },
    onProcessingError(message, uploaded) {
      page.setData({
        error: message,
        newAudioSummary: uploaded?.durationText ? `已保存 ${uploaded.durationText}` : "",
        inputDraftDirty: true,
      });
      page.persistInputDraft();
    },
    onProcessingDone() {
      page.setData({ uploadingAudio: false, transcribingAudio: false });
    },
    onRecorderError(message) {
      page.setData({
        recording: false,
        uploadingAudio: false,
        transcribingAudio: false,
        error: message,
      });
    },
  };
}

Page<QaDetailData, QaDetailMethods>({
  data: {
    answerId: "",
    questionId: "",
    loading: false,
    saving: false,
    submitting: false,
    recording: false,
    uploadingAudio: false,
    transcribingAudio: false,
    playing: false,
    error: "",
    sessionTitle: "",
    questionPrompt: "",
    transcriptRaw: "",
    answerText: "",
    hasAudio: false,
    audioLabel: "未保存录音",
    audioTempUrl: "",
    audioError: "",
    newAudioFileId: "",
    newAudioDurationSeconds: null,
    newAudioSummary: "",
    inputDraftDirty: false,
  },

  async onLoad(query) {
    const answerId = query?.answerId || "";
    this.setData({ answerId });
    const snapshot = await this.hydrateFromCache();
    if (shouldRefreshMemoirStoryDetailSnapshot(answerId) || !snapshot.hasDetail) {
      await this.loadDetail();
    }
  },

  onHide() {
    this.persistInputDraft();
  },

  onUnload() {
    this.persistInputDraft();
    if (this.audioRecordingSession) {
      clearMemoirAudioRecordingSession(this.audioRecordingSession);
      this.audioRecordingSession = undefined;
    }
    if (this.data.recording) {
      stopMemoirAudioRecording();
    }
    if (audioContext) {
      audioContext.stop();
      audioContext.destroy();
      audioContext = null;
    }
  },

  async hydrateFromCache() {
    const snapshot = readMemoirStoryDetailSnapshot(this.data.answerId);
    if (!snapshot.detail) {
      return snapshot;
    }
    let audioTempUrl = "";
    let audioError = "";
    if (snapshot.detail.audio_file?.file_id) {
      try {
        audioTempUrl = await getTempFileUrl(snapshot.detail.audio_file.file_id);
      } catch (error) {
        audioError = error instanceof Error ? error.message : "录音播放地址生成失败";
      }
    }
    this.applyDetail(snapshot.detail, audioTempUrl, audioError);
    this.applyInputDraft(this.data.answerId);
    return snapshot;
  },

  applyInputDraft(answerId) {
    const inputDraft = readMemoirQaDetailInputDraft(answerId);
    if (!inputDraft) {
      return false;
    }
    this.setData({
      answerText: inputDraft.answer_text,
      newAudioFileId: inputDraft.new_audio_file_id || "",
      newAudioDurationSeconds: inputDraft.new_audio_duration_seconds ?? null,
      newAudioSummary: inputDraft.new_audio_summary || "",
      inputDraftDirty: true,
    });
    return true;
  },

  persistInputDraft() {
    if (!this.data.inputDraftDirty) {
      return;
    }
    const inputDraft = inputDraftFromData(this.data);
    if (inputDraft) {
      writeMemoirQaDetailInputDraft(inputDraft);
    }
  },

  async loadDetail() {
    if (!this.data.answerId) {
      this.setData({ error: "缺少问答信息" });
      return;
    }
    this.setData({ loading: true, error: "", audioError: "" });
    try {
      const detail = await getMemoirStoryDetail(this.data.answerId);
      let audioTempUrl = "";
      let audioError = "";
      if (detail.audio_file?.file_id) {
        try {
          audioTempUrl = await getTempFileUrl(detail.audio_file.file_id);
        } catch (error) {
          audioError = error instanceof Error ? error.message : "录音播放地址生成失败";
        }
      }
      writeMemoirStoryDetailSnapshot(detail);
      this.applyDetail(detail, audioTempUrl, audioError);
      this.applyInputDraft(detail.answer.id);
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "问答详情加载失败" });
    } finally {
      this.setData({ loading: false });
    }
  },

  applyDetail(detail, audioTempUrl, audioError) {
    const audioFile = detail.audio_file || null;
    this.setData({
      answerId: detail.answer.id,
      questionId: detail.question.id,
      sessionTitle: detail.story.session_title,
      questionPrompt: detail.question.prompt,
      transcriptRaw: detail.answer.transcript_raw || "",
      answerText: detail.answer.transcript_edited || detail.answer.transcript_raw || "",
      hasAudio: Boolean(audioFile?.file_id || detail.answer.audio_file_id),
      audioLabel: audioLabel(Boolean(audioFile?.file_id || detail.answer.audio_file_id), audioFile?.duration_seconds ?? detail.answer.duration_seconds),
      audioTempUrl,
      audioError,
      inputDraftDirty: false,
    });
  },

  onAnswerInput(event) {
    this.setData({ answerText: event.detail.value, inputDraftDirty: true });
    this.persistInputDraft();
  },

  async saveText() {
    const text = this.data.answerText.trim();
    if (!text || this.data.saving || isAudioBusy(this.data)) {
      return;
    }
    this.setData({ saving: true, error: "" });
    try {
      const answer = await updateTextAnswer(this.data.answerId, text);
      updateMemoirCachedStoryAnswer(answer);
      markMemoirDashboardSnapshotStale();
      this.setData({
        transcriptRaw: answer.transcript_raw || this.data.transcriptRaw,
        answerText: answer.transcript_edited || text,
      });
      if (this.data.newAudioFileId || this.data.newAudioSummary) {
        this.setData({ inputDraftDirty: true });
        this.persistInputDraft();
      } else {
        clearMemoirQaDetailInputDraft(this.data.answerId);
        this.setData({ inputDraftDirty: false });
      }
      wx.showToast({ title: "已保存", icon: "success" });
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "文字保存失败" });
    } finally {
      this.setData({ saving: false });
    }
  },

  async submitReanswer() {
    const text = this.data.answerText.trim();
    if (!text || !this.data.questionId || this.data.submitting || isAudioBusy(this.data)) {
      return;
    }
    this.setData({ submitting: true, error: "" });
    try {
      const previousAnswerId = this.data.answerId;
      const answer = await createTextAnswer(
        this.data.questionId,
        text,
        this.data.newAudioFileId || undefined,
        this.data.newAudioDurationSeconds ?? undefined,
      );
      updateMemoirCachedStoryAnswer(answer);
      markMemoirDashboardSnapshotStale();
      markMemoirStoriesSnapshotStale();
      this.setData({
        answerId: answer.id,
        newAudioFileId: "",
        newAudioDurationSeconds: null,
        newAudioSummary: "",
        inputDraftDirty: false,
      });
      clearMemoirQaDetailInputDraft(previousAnswerId);
      clearMemoirQaDetailInputDraft(answer.id);
      await this.loadDetail();
      wx.showToast({ title: "已重答", icon: "success" });
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "重答提交失败" });
    } finally {
      this.setData({ submitting: false });
    }
  },

  startRecording() {
    if (this.data.loading || this.data.saving || this.data.submitting || isAudioBusy(this.data)) {
      return;
    }
    this.setData({
      error: "",
      newAudioFileId: "",
      newAudioDurationSeconds: null,
      newAudioSummary: "",
      inputDraftDirty: true,
    });
    this.persistInputDraft();
    this.audioRecordingSession = createQaDetailRecorderSession(this);
    startMemoirAudioRecording(this.audioRecordingSession);
  },

  stopRecording() {
    if (!this.data.recording) {
      return;
    }
    stopMemoirAudioRecording();
  },

  clearRecordedAudio() {
    if (isAudioBusy(this.data)) {
      return;
    }
    this.setData({
      newAudioFileId: "",
      newAudioDurationSeconds: null,
      newAudioSummary: "",
      inputDraftDirty: true,
    });
    this.persistInputDraft();
  },

  playAudio() {
    if (!this.data.audioTempUrl) {
      this.setData({ audioError: this.data.audioError || "这条问答没有可播放的录音" });
      return;
    }
    if (this.data.playing) {
      this.stopAudio();
      return;
    }
    const context = ensureAudioContext(this);
    context.stop();
    context.src = this.data.audioTempUrl;
    this.setData({ playing: true, audioError: "" });
    context.play();
  },

  stopAudio() {
    if (audioContext) {
      audioContext.stop();
    }
    this.setData({ playing: false });
  },

  backQa() {
    wx.navigateBack({
      delta: 1,
      fail: () => wx.redirectTo({ url: "/pages/qa/index" }),
    });
  },
});
