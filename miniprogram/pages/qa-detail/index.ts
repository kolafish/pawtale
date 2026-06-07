import type { MemoirStoryDetail } from "../../domain/memoir";
import {
  createTextAnswer,
  getMemoirStoryDetail,
  transcribeMemoirAudioFile,
  updateTextAnswer,
  uploadMemoirAudioFile,
} from "../../services/memoir";
import {
  markMemoirDashboardSnapshotStale,
  markMemoirStoriesSnapshotStale,
  readMemoirStoryDetailSnapshot,
  shouldRefreshMemoirStoryDetailSnapshot,
  type MemoirStoryDetailSnapshot,
  updateMemoirCachedStoryAnswer,
  writeMemoirStoryDetailSnapshot,
} from "../../stores/memoir-cache";

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
}

interface QaDetailMethods {
  onLoad(query?: Record<string, string | undefined>): Promise<void>;
  onUnload(): void;
  hydrateFromCache(): Promise<MemoirStoryDetailSnapshot>;
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

interface AudioContextLike {
  src: string;
  play(): void;
  stop(): void;
  destroy(): void;
  onEnded(callback: () => void): void;
  onError(callback: (error: { errMsg?: string }) => void): void;
}

interface CloudWithTempFileUrl {
  getTempFileURL(options: {
    fileList: string[];
    success(result: { fileList: Array<{ tempFileURL?: string }> }): void;
    fail(error: { errMsg?: string }): void;
  }): void;
}

interface WxWithAudioContext {
  createInnerAudioContext(): AudioContextLike;
}

interface WxWithNavigation {
  navigateBack(options: {
    delta: number;
    fail(): void;
  }): void;
}

const recorderManager = wx.getRecorderManager();
const MAX_RECORDING_MS = 60000;
let recorderBound = false;
let recordingStartedAt = 0;
let activePage: { setData(data: Partial<QaDetailData>): void } | null = null;
let audioContext: AudioContextLike | null = null;

function durationSeconds(durationMs: number): number {
  return Math.max(0, Math.round(durationMs / 1000));
}

function durationText(seconds?: number | null): string {
  if (typeof seconds !== "number" || seconds <= 0) {
    return "";
  }
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  return `${minutes}:${rest < 10 ? "0" : ""}${rest}`;
}

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

function getTempFileUrl(fileId: string): Promise<string> {
  return new Promise((resolve, reject) => {
    (wx.cloud as unknown as CloudWithTempFileUrl).getTempFileURL({
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

function ensureAudioContext(page: { setData(data: Partial<QaDetailData>): void }): AudioContextLike {
  if (audioContext) {
    return audioContext;
  }
  audioContext = (wx as unknown as WxWithAudioContext).createInnerAudioContext();
  audioContext.onEnded(() => {
    page.setData({ playing: false });
  });
  audioContext.onError((error) => {
    page.setData({ playing: false, audioError: error.errMsg || "录音播放失败" });
  });
  return audioContext;
}

function bindRecorderEvents(): void {
  if (recorderBound) {
    return;
  }
  recorderManager.onStart(() => {
    recordingStartedAt = Date.now();
    activePage?.setData({ recording: true, error: "" });
  });
  recorderManager.onStop(async (result) => {
    const page = activePage;
    if (!page) {
      return;
    }
    page.setData({ recording: false });
    if (!result.tempFilePath) {
      page.setData({ error: "录音保存失败" });
      return;
    }

    const seconds = durationSeconds(result.duration || Date.now() - recordingStartedAt);
    page.setData({ uploadingAudio: true, error: "", newAudioSummary: "正在保存录音" });
    let savedDurationText = "";
    try {
      const audioFile = await uploadMemoirAudioFile(result.tempFilePath, seconds, result.fileSize);
      const savedSeconds = audioFile.duration_seconds ?? seconds;
      savedDurationText = durationText(savedSeconds);
      page.setData({
        newAudioFileId: audioFile.id,
        newAudioDurationSeconds: savedSeconds,
        uploadingAudio: false,
        transcribingAudio: true,
        newAudioSummary: "正在转写",
      });
      const transcription = await transcribeMemoirAudioFile(audioFile.id);
      page.setData({
        answerText: transcription.text,
        newAudioSummary: `已转写 ${savedDurationText}`,
      });
    } catch (error) {
      page.setData({
        error: error instanceof Error ? error.message : "录音处理失败",
        newAudioSummary: savedDurationText ? `已保存 ${savedDurationText}` : "",
      });
    } finally {
      page.setData({ uploadingAudio: false, transcribingAudio: false });
    }
  });
  recorderManager.onError((error) => {
    activePage?.setData({
      recording: false,
      uploadingAudio: false,
      transcribingAudio: false,
      error: error.errMsg || "录音失败",
    });
  });
  recorderBound = true;
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
  },

  async onLoad(query) {
    activePage = this;
    bindRecorderEvents();
    const answerId = query?.answerId || "";
    this.setData({ answerId });
    const snapshot = await this.hydrateFromCache();
    if (shouldRefreshMemoirStoryDetailSnapshot(answerId) || !snapshot.hasDetail) {
      await this.loadDetail();
    }
  },

  onUnload() {
    if (activePage === this) {
      activePage = null;
    }
    if (this.data.recording) {
      recorderManager.stop();
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
    return snapshot;
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
    });
  },

  onAnswerInput(event) {
    this.setData({ answerText: event.detail.value });
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
      });
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
    });
    recorderManager.start({
      duration: MAX_RECORDING_MS,
      sampleRate: 16000,
      numberOfChannels: 1,
      encodeBitRate: 48000,
      format: "mp3",
    });
  },

  stopRecording() {
    if (!this.data.recording) {
      return;
    }
    recorderManager.stop();
  },

  clearRecordedAudio() {
    if (isAudioBusy(this.data)) {
      return;
    }
    this.setData({ newAudioFileId: "", newAudioDurationSeconds: null, newAudioSummary: "" });
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
    (wx as unknown as WxWithNavigation).navigateBack({
      delta: 1,
      fail: () => wx.redirectTo({ url: "/pages/qa/index" }),
    });
  },
});
