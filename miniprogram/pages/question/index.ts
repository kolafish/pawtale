import type { MemoirQuestion } from "../../domain/memoir";
import {
  createTextAnswer,
  getCurrentSession,
  getNextQuestion,
  skipQuestion,
  transcribeMemoirAudioFile,
  uploadMemoirAudioFile,
} from "../../services/memoir";
import { markMemoirDataSnapshotsStale } from "../../stores/memoir-cache";
import {
  clearMemoirQuestionInputDraft,
  readMemoirQuestionInputDraft,
  writeMemoirQuestionInputDraft,
} from "../../stores/memoir-input-drafts";
import { clearMemoirDraft, writeMemoirStore } from "../../stores/session";

interface InputEvent {
  detail: {
    value: string;
  };
}

interface QuestionPageOptions {
  questionId?: string;
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
  audioFileId: string;
  audioDurationSeconds: number | null;
  audioSummary: string;
  isCompleted: boolean;
}

const recorderManager = wx.getRecorderManager();
const MAX_RECORDING_MS = 60000;
let recorderBound = false;
let recordingStartedAt = 0;
type QuestionPageInstance = {
  data: QuestionData;
  setData(data: Partial<QuestionData>): void;
  persistInputDraft(): void;
};
let activePage: QuestionPageInstance | null = null;

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

function inputDraftFromData(data: QuestionData): Parameters<typeof writeMemoirQuestionInputDraft>[0] | null {
  if (!data.question) {
    return null;
  }
  return {
    question_id: data.question.id,
    answer_text: data.answerText,
    audio_file_id: data.audioFileId || undefined,
    audio_duration_seconds: data.audioDurationSeconds,
    audio_summary: data.audioSummary || undefined,
  };
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
      const savedSeconds = audioFile.duration_seconds ?? seconds;
      savedDurationText = durationText(savedSeconds);
      page.setData({
        audioFileId: audioFile.id,
        audioDurationSeconds: savedSeconds,
        uploadingAudio: false,
        transcribingAudio: true,
        audioSummary: "正在转写",
      });
      page.persistInputDraft();
      const transcription = await transcribeMemoirAudioFile(audioFile.id);
      page.setData({
        answerText: transcription.text,
        audioSummary: `已转写 ${savedDurationText}`,
      });
      page.persistInputDraft();
    } catch (error) {
      page.setData({
        error: error instanceof Error ? error.message : "录音处理失败",
        audioSummary: savedDurationText ? `已保存 ${savedDurationText}` : "",
      });
      page.persistInputDraft();
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
  onLoad(options?: QuestionPageOptions): Promise<void>;
  onHide(): void;
  onUnload(): void;
  loadQuestion(preferredQuestionId?: string): Promise<void>;
  persistInputDraft(): void;
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
    audioFileId: "",
    audioDurationSeconds: null,
    audioSummary: "",
    isCompleted: false,
  },

  async onLoad(options = {}) {
    activePage = this;
    bindRecorderEvents();
    await this.loadQuestion(options.questionId);
  },

  onHide() {
    this.persistInputDraft();
  },

  onUnload() {
    this.persistInputDraft();
    if (activePage === this) {
      activePage = null;
    }
    if (this.data.recording) {
      recorderManager.stop();
    }
  },

  async loadQuestion(preferredQuestionId = "") {
    this.setData({
      loading: true,
      error: "",
      answerText: "",
      audioFileId: "",
      audioDurationSeconds: null,
      audioSummary: "",
    });
    try {
      const interview = await getCurrentSession();
      const preferredQuestion = preferredQuestionId
        ? interview.questions.find((item) => item.id === preferredQuestionId && item.status === "pending")
        : undefined;
      const nextQuestion = preferredQuestion ? null : await getNextQuestion();
      const current = preferredQuestion || nextQuestion || interview.current_question;
      const questions = current && !interview.questions.some((item) => item.id === current.id)
        ? [...interview.questions, current]
        : interview.questions;
      writeMemoirStore({
        interview: {
          ...interview,
          session: {
            ...interview.session,
            current_question_id: current?.id,
            status: current ? "active" : "completed",
          },
          current_question: current,
          questions,
        },
      });
      this.setData({
        sessionTitle: interview.session.title,
        question: current,
        prompt: current?.prompt || "",
        isCompleted: !current,
      });
      if (current) {
        const inputDraft = readMemoirQuestionInputDraft(current.id);
        if (inputDraft) {
          this.setData({
            answerText: inputDraft.answer_text,
            audioFileId: inputDraft.audio_file_id || "",
            audioDurationSeconds: inputDraft.audio_duration_seconds ?? null,
            audioSummary: inputDraft.audio_summary || "",
          });
        }
      }
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "问题加载失败" });
    } finally {
      this.setData({ loading: false });
    }
  },

  persistInputDraft() {
    const inputDraft = inputDraftFromData(this.data);
    if (inputDraft) {
      writeMemoirQuestionInputDraft(inputDraft);
    }
  },

  onAnswerInput(event) {
    this.setData({ answerText: event.detail.value });
    this.persistInputDraft();
  },

  startRecording() {
    if (this.data.loading || this.data.isCompleted || this.data.submitting || isAudioBusy(this.data)) {
      return;
    }
    this.setData({ error: "", audioFileId: "", audioDurationSeconds: null, audioSummary: "" });
    this.persistInputDraft();
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
    this.setData({ audioFileId: "", audioDurationSeconds: null, audioSummary: "" });
    this.persistInputDraft();
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
      const answer = await createTextAnswer(
        question.id,
        text,
        this.data.audioFileId || undefined,
        this.data.audioDurationSeconds ?? undefined,
      );
      clearMemoirDraft();
      markMemoirDataSnapshotsStale();
      writeMemoirStore({ question, answer });
      clearMemoirQuestionInputDraft(question.id);
      this.setData({ answerText: "", audioFileId: "", audioDurationSeconds: null, audioSummary: "" });
      wx.navigateTo({ url: `/pages/review/index?answerId=${answer.id}` });
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
      markMemoirDataSnapshotsStale();
      writeMemoirStore({ interview });
      clearMemoirQuestionInputDraft(question.id);
      await this.loadQuestion();
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "跳过失败" });
    } finally {
      this.setData({ submitting: false });
    }
  },
});
