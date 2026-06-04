import type { MemoirQuestion } from "../../domain/memoir";
import {
  createTextAnswer,
  getCurrentSession,
  skipQuestion,
  transcribeMemoirAudioFile,
  uploadMemoirAudioFile,
} from "../../services/memoir";
import { clearMemoirDraft, writeMemoirStore } from "../../stores/session";

interface InputEvent {
  detail: {
    value: string;
  };
}

interface QuestionData {
  loading: boolean;
  submitting: boolean;
  recording: boolean;
  uploadingAudio: boolean;
  transcribingAudio: boolean;
  error: string;
  sessionTitle: string;
  question?: MemoirQuestion;
  prompt: string;
  answerText: string;
  audioSummary: string;
  isCompleted: boolean;
}

const recorderManager = wx.getRecorderManager();
const MAX_RECORDING_MS = 60000;
let recorderBound = false;
let recordingStartedAt = 0;
let activePage: { setData(data: Partial<QuestionData>): void } | null = null;

function isAudioBusy(data: Pick<QuestionData, "recording" | "uploadingAudio" | "transcribingAudio">): boolean {
  return data.recording || data.uploadingAudio || data.transcribingAudio;
}

function durationSeconds(durationMs: number): number {
  return Math.max(0, Math.round(durationMs / 1000));
}

function durationText(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return `${minutes}:${rest < 10 ? "0" : ""}${rest}`;
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
    page.setData({ uploadingAudio: true, error: "", audioSummary: "正在保存录音" });
    let savedDurationText = "";
    try {
      const audioFile = await uploadMemoirAudioFile(result.tempFilePath, seconds, result.fileSize);
      savedDurationText = durationText(audioFile.duration_seconds ?? seconds);
      page.setData({
        uploadingAudio: false,
        transcribingAudio: true,
        audioSummary: "正在转写",
      });
      const transcription = await transcribeMemoirAudioFile(audioFile.id);
      page.setData({
        answerText: transcription.text,
        audioSummary: `已转写 ${savedDurationText}`,
      });
    } catch (error) {
      page.setData({
        error: error instanceof Error ? error.message : "录音处理失败",
        audioSummary: savedDurationText ? `已保存 ${savedDurationText}` : "",
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

Page<QuestionData, {
  onLoad(): Promise<void>;
  onUnload(): void;
  loadQuestion(): Promise<void>;
  onAnswerInput(event: InputEvent): void;
  startRecording(): void;
  stopRecording(): void;
  clearRecordedAudio(): void;
  submitAnswer(): Promise<void>;
  skipCurrentQuestion(): Promise<void>;
}>({
  data: {
    loading: false,
    submitting: false,
    recording: false,
    uploadingAudio: false,
    transcribingAudio: false,
    error: "",
    sessionTitle: "回忆录访谈",
    question: undefined,
    prompt: "",
    answerText: "",
    audioSummary: "",
    isCompleted: false,
  },

  async onLoad() {
    activePage = this;
    bindRecorderEvents();
    await this.loadQuestion();
  },

  onUnload() {
    if (activePage === this) {
      activePage = null;
    }
    if (this.data.recording) {
      recorderManager.stop();
    }
  },

  async loadQuestion() {
    this.setData({
      loading: true,
      error: "",
      answerText: "",
      audioSummary: "",
    });
    try {
      const interview = await getCurrentSession();
      writeMemoirStore({ interview });
      const current = interview.current_question;
      this.setData({
        sessionTitle: interview.session.title,
        question: current,
        prompt: current?.prompt || "",
        isCompleted: !current,
      });
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "问题加载失败" });
    } finally {
      this.setData({ loading: false });
    }
  },

  onAnswerInput(event) {
    this.setData({ answerText: event.detail.value });
  },

  startRecording() {
    if (this.data.loading || this.data.isCompleted || this.data.submitting || isAudioBusy(this.data)) {
      return;
    }
    this.setData({ error: "", audioSummary: "" });
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
    this.setData({ audioSummary: "" });
  },

  async submitAnswer() {
    const text = this.data.answerText.trim();
    const question = this.data.question;
    if (!question || !text) {
      wx.showToast({ title: "请先输入回答", icon: "none" });
      return;
    }

    if (isAudioBusy(this.data)) {
      wx.showToast({ title: "录音处理中", icon: "none" });
      return;
    }

    this.setData({ submitting: true, error: "" });
    try {
      const answer = await createTextAnswer(question.id, text);
      clearMemoirDraft();
      writeMemoirStore({ question, answer });
      wx.navigateTo({ url: `/pages/feedback/index?answerId=${answer.id}` });
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "提交失败" });
    } finally {
      this.setData({ submitting: false });
    }
  },

  async skipCurrentQuestion() {
    const question = this.data.question;
    if (isAudioBusy(this.data)) {
      wx.showToast({ title: "录音处理中", icon: "none" });
      return;
    }
    if (!question) {
      return;
    }
    this.setData({ submitting: true, error: "" });
    try {
      const interview = await skipQuestion(question.id);
      writeMemoirStore({ interview });
      await this.loadQuestion();
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "跳过失败" });
    } finally {
      this.setData({ submitting: false });
    }
  },
});
