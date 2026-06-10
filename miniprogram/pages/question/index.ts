import type {
  MemoirAnswerSegment,
  MemoirAnswerSegmentStatus,
  MemoirQuestion,
} from "../../domain/memoir";
import {
  appendMemoirAnswerSegment,
  createMemoirAnswerSession,
  createMemoirSegmentTranscriptionJob,
  getCurrentSession,
  getNextQuestion,
  processMemoirAsrJob,
  skipQuestion,
  submitMemoirAnswerSession,
  updateMemoirAnswerSegment,
} from "../../services/memoir";
import {
  clearMemoirAudioRecordingSession,
  durationText,
  type MemoirAudioRecorderSession,
  type MemoirAudioRecorderUploaded,
  startMemoirAudioRecording,
  stopMemoirAudioRecording,
} from "../../services/memoir-audio-recorder";
import {
  markMemoirDataSnapshotsStale,
  readMemoirDashboardSnapshot,
} from "../../stores/memoir-cache";
import {
  clearMemoirAnswerSessionDraft,
  readMemoirAnswerSessionDraft,
  type MemoirAnswerSessionDraftSegment,
  writeMemoirAnswerSessionDraft,
  writeMemoirAnswerSessionDraftFromRemote,
} from "../../stores/memoir-answer-session-drafts";
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

interface SegmentInputEvent extends InputEvent {
  currentTarget: {
    dataset: {
      localId?: string;
    };
  };
}

interface QuestionPageOptions {
  questionId?: string;
}

interface QuestionAnswerSegmentView {
  localId: string;
  answerSegmentId?: string;
  audioFileId?: string | null;
  sortOrder: number;
  status: MemoirAnswerSegmentStatus | "local" | "uploading" | "transcribing" | "submitted";
  statusText: string;
  durationSeconds: number | null;
  durationLabel: string;
  text: string;
  errorMessage: string;
  dirty: boolean;
  createdAt: string;
  updatedAt: string;
}

interface QuestionData {
  loading: boolean;
  submitting: boolean;
  recording: boolean;
  recordingElapsedSeconds: number;
  recordingElapsedLabel: string;
  uploadingAudio: boolean;
  transcribingAudio: boolean;
  syncingSegment: boolean;
  processingSegmentCount: number;
  processingSegmentLabel: string;
  error: string;
  projectId: string;
  sessionId: string;
  sessionTitle: string;
  question?: MemoirQuestion;
  prompt: string;
  answerSessionId: string;
  answerSessionCreatedAt: string;
  answerSegments: QuestionAnswerSegmentView[];
  answerText: string;
  legacyAudioFileId: string;
  legacyAudioDurationSeconds: number | null;
  audioSummary: string;
  isCompleted: boolean;
}

type QuestionPageInstance = {
  data: QuestionData;
  setData(data: Partial<QuestionData>): void;
  persistInputDraft(): void;
  persistAnswerSessionDraft(): void;
  ensureAnswerSession(): Promise<string>;
  appendRecordedSegment(uploaded: MemoirAudioRecorderUploaded, transcriptText: string, errorMessage?: string): Promise<void>;
  beginSegmentProcessing(message: string): void;
  endSegmentProcessing(): void;
  audioRecordingSession?: MemoirAudioRecorderSession;
};

let recordingTimer: ReturnType<typeof setInterval> | null = null;
let recordingTimerStartedAt = 0;

function nowIso(): string {
  return new Date().toISOString();
}

function isAudioBusy(data: Pick<QuestionData, "recording" | "uploadingAudio" | "transcribingAudio" | "syncingSegment" | "processingSegmentCount">): boolean {
  return data.recording || data.uploadingAudio || data.transcribingAudio || data.syncingSegment || data.processingSegmentCount > 0;
}

function recordingElapsedText(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  const rest = Math.max(0, seconds % 60);
  return `${minutes}:${rest < 10 ? "0" : ""}${rest}`;
}

function processingSegmentText(count: number): string {
  if (count <= 0) {
    return "";
  }
  return count > 1 ? `${count} 段正在保存或转写` : "上一段正在保存或转写";
}

function stopRecordingTimer(): void {
  if (!recordingTimer) {
    return;
  }
  clearInterval(recordingTimer);
  recordingTimer = null;
}

function startRecordingTimer(page: { setData(data: Partial<QuestionData>): void }): void {
  stopRecordingTimer();
  recordingTimerStartedAt = Date.now();
  page.setData({
    recordingElapsedSeconds: 0,
    recordingElapsedLabel: recordingElapsedText(0),
  });
  recordingTimer = setInterval(() => {
    const seconds = Math.max(0, Math.floor((Date.now() - recordingTimerStartedAt) / 1000));
    page.setData({
      recordingElapsedSeconds: seconds,
      recordingElapsedLabel: recordingElapsedText(seconds),
    });
  }, 1000);
}

function segmentStatusText(status: QuestionAnswerSegmentView["status"], dirty = false): string {
  if (dirty) {
    return "已修改";
  }
  const labels: Record<string, string> = {
    local: "待提交",
    uploading: "保存中",
    uploaded: "已保存",
    transcribing: "转写中",
    transcribed: "已转写",
    failed: "需处理",
    submitted: "已提交",
    draft: "草稿",
  };
  return labels[status] || "草稿";
}

function segmentText(segment: QuestionAnswerSegmentView): string {
  return segment.text.trim();
}

function segmentViewFromRemote(segment: MemoirAnswerSegment): QuestionAnswerSegmentView {
  const text = segment.transcript_edited || segment.transcript_raw || "";
  return {
    localId: `remote:${segment.id}`,
    answerSegmentId: segment.id,
    audioFileId: segment.audio_file_id,
    sortOrder: segment.sort_order,
    status: segment.status,
    statusText: segmentStatusText(segment.status),
    durationSeconds: segment.duration_seconds ?? null,
    durationLabel: durationText(segment.duration_seconds),
    text,
    errorMessage: segment.error_message || "",
    dirty: false,
    createdAt: segment.created_at,
    updatedAt: segment.updated_at,
  };
}

function segmentViewFromDraft(segment: MemoirAnswerSessionDraftSegment): QuestionAnswerSegmentView {
  const text = segment.transcript_edited || segment.transcript_raw || "";
  return {
    localId: segment.local_id,
    answerSegmentId: segment.answer_segment_id,
    audioFileId: segment.audio_file_id,
    sortOrder: segment.sort_order,
    status: segment.status,
    statusText: segmentStatusText(segment.status),
    durationSeconds: segment.duration_seconds ?? null,
    durationLabel: durationText(segment.duration_seconds),
    text,
    errorMessage: segment.error_message || "",
    dirty: Boolean(segment.dirty),
    createdAt: segment.created_at,
    updatedAt: segment.updated_at,
  };
}

function draftSegmentFromView(segment: QuestionAnswerSegmentView): MemoirAnswerSessionDraftSegment {
  return {
    local_id: segment.localId,
    answer_segment_id: segment.answerSegmentId,
    audio_file_id: segment.audioFileId,
    sort_order: segment.sortOrder,
    status: segment.status,
    duration_seconds: segment.durationSeconds,
    transcript_raw: segment.text,
    transcript_edited: segment.text,
    error_message: segment.errorMessage,
    dirty: segment.dirty,
    created_at: segment.createdAt,
    updated_at: segment.updatedAt,
  };
}

function sortSegments(segments: QuestionAnswerSegmentView[]): QuestionAnswerSegmentView[] {
  return [...segments].sort((left, right) => left.sortOrder - right.sortOrder);
}

function upsertSegmentView(
  segments: QuestionAnswerSegmentView[],
  segment: MemoirAnswerSegment,
): QuestionAnswerSegmentView[] {
  const view = segmentViewFromRemote(segment);
  const index = segments.findIndex((item) => item.answerSegmentId === segment.id);
  if (index === -1) {
    return sortSegments([...segments, view]);
  }
  const nextSegments = [...segments];
  nextSegments[index] = view;
  return sortSegments(nextSegments);
}

function inputDraftFromData(data: QuestionData): Parameters<typeof writeMemoirQuestionInputDraft>[0] | null {
  if (!data.question) {
    return null;
  }
  return {
    question_id: data.question.id,
    answer_text: data.answerText,
    audio_file_id: data.legacyAudioFileId || undefined,
    audio_duration_seconds: data.legacyAudioDurationSeconds,
    audio_summary: data.legacyAudioFileId ? data.audioSummary || undefined : undefined,
  };
}

function createQuestionRecorderSession(page: QuestionPageInstance): MemoirAudioRecorderSession {
  return {
    continuous: true,
    transcribeAfterUpload: false,
    onStart() {
      page.setData({ recording: true, error: "", audioSummary: "" });
      startRecordingTimer(page);
    },
    onStop(autoRestarting = false) {
      stopRecordingTimer();
      page.setData({
        recording: autoRestarting,
        recordingElapsedSeconds: 0,
        recordingElapsedLabel: recordingElapsedText(0),
      });
    },
    onAutoSegmentStop(result) {
      page.setData({
        audioSummary: `已录满 ${result.durationText}，这一段正在保存，已自动继续录音`,
      });
    },
    onMissingFile() {
      page.setData({ error: "录音保存失败", audioSummary: "" });
    },
    onUploadStart() {
      page.beginSegmentProcessing(page.data.recording ? "上一段正在保存，当前可以继续讲" : "正在保存录音");
    },
    async onUploaded(result) {
      await page.appendRecordedSegment(result, "");
    },
    onTranscribed(result) {
      void page.appendRecordedSegment(result, result.transcription.text);
    },
    onProcessingError(message, uploaded) {
      page.setData({
        error: message,
        audioSummary: uploaded?.durationText ? `已保存 ${uploaded.durationText}` : "",
      });
      if (uploaded) {
        void page.appendRecordedSegment(uploaded, "", message);
      }
    },
    onProcessingDone() {
      page.endSegmentProcessing();
    },
    onRecorderError(message) {
      stopRecordingTimer();
      page.setData({
        recording: false,
        recordingElapsedSeconds: 0,
        recordingElapsedLabel: recordingElapsedText(0),
        uploadingAudio: false,
        transcribingAudio: false,
    syncingSegment: false,
    processingSegmentCount: 0,
    processingSegmentLabel: "",
    audioSummary: "",
        error: message,
      });
    },
  };
}

Page<QuestionData, {
  audioRecordingSession?: MemoirAudioRecorderSession;
  onLoad(options?: QuestionPageOptions): Promise<void>;
  onHide(): void;
  onUnload(): void;
  hydrateQuestionFromCache(preferredQuestionId: string): boolean;
  loadQuestion(preferredQuestionId?: string): Promise<void>;
  persistInputDraft(): void;
  persistAnswerSessionDraft(): void;
  ensureAnswerSession(): Promise<string>;
  appendRecordedSegment(uploaded: MemoirAudioRecorderUploaded, transcriptText: string, errorMessage?: string): Promise<void>;
  beginSegmentProcessing(message: string): void;
  endSegmentProcessing(): void;
  syncDirtyAnswerSegments(): Promise<QuestionAnswerSegmentView[]>;
  onAnswerInput(event: InputEvent): void;
  onSegmentInput(event: SegmentInputEvent): void;
  startRecording(): void;
  stopRecording(): void;
  submitAnswer(): Promise<void>;
  skipCurrentQuestion(): Promise<void>;
}>({
  data: {
    loading: false,
    submitting: false,
    recording: false,
    recordingElapsedSeconds: 0,
    recordingElapsedLabel: "0:00",
    uploadingAudio: false,
    transcribingAudio: false,
    syncingSegment: false,
    processingSegmentCount: 0,
    processingSegmentLabel: "",
    error: "",
    projectId: "",
    sessionId: "",
    sessionTitle: "回忆录访谈",
    question: undefined,
    prompt: "",
    answerSessionId: "",
    answerSessionCreatedAt: "",
    answerSegments: [],
    answerText: "",
    legacyAudioFileId: "",
    legacyAudioDurationSeconds: null,
    audioSummary: "",
    isCompleted: false,
  },

  async onLoad(options = {}) {
    if (options.questionId && this.hydrateQuestionFromCache(options.questionId)) {
      return;
    }
    await this.loadQuestion(options.questionId);
  },

  onHide() {
    this.persistInputDraft();
    this.persistAnswerSessionDraft();
  },

  onUnload() {
    this.persistInputDraft();
    this.persistAnswerSessionDraft();
    if (this.audioRecordingSession) {
      clearMemoirAudioRecordingSession(this.audioRecordingSession);
      this.audioRecordingSession = undefined;
    }
    stopRecordingTimer();
    if (this.data.recording) {
      stopMemoirAudioRecording();
    }
  },

  hydrateQuestionFromCache(preferredQuestionId) {
    const snapshot = readMemoirDashboardSnapshot();
    const interview = snapshot.interview;
    if (!interview || !preferredQuestionId) {
      return false;
    }
    const questions = interview.current_question
      ? [interview.current_question, ...interview.questions]
      : interview.questions;
    const current = questions.find((item) => item.id === preferredQuestionId && item.status === "pending");
    if (!current) {
      return false;
    }

    const projectId = interview.session.project_id;
    const answerDraft = readMemoirAnswerSessionDraft(projectId, current.id);
    const inputDraft = readMemoirQuestionInputDraft(current.id);
    this.setData({
      loading: false,
      error: "",
      projectId,
      sessionId: interview.session.id,
      sessionTitle: interview.session.title,
      question: current,
      prompt: current.prompt,
      isCompleted: false,
      answerSessionId: answerDraft?.answer_session_id || "",
      answerSessionCreatedAt: answerDraft?.created_at || "",
      answerSegments: answerDraft ? answerDraft.segments.map(segmentViewFromDraft) : [],
      answerText: inputDraft?.answer_text || "",
      legacyAudioFileId: inputDraft?.audio_file_id || "",
      legacyAudioDurationSeconds: inputDraft?.audio_duration_seconds ?? null,
      audioSummary: inputDraft?.audio_summary || "",
    });
    return true;
  },

  async loadQuestion(preferredQuestionId = "") {
    this.setData({
      loading: true,
      error: "",
      answerText: "",
      answerSessionId: "",
      answerSessionCreatedAt: "",
      answerSegments: [],
      legacyAudioFileId: "",
      legacyAudioDurationSeconds: null,
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

      const projectId = interview.session.project_id;
      const answerDraft = current ? readMemoirAnswerSessionDraft(projectId, current.id) : undefined;
      const inputDraft = current ? readMemoirQuestionInputDraft(current.id) : undefined;
      this.setData({
        projectId,
        sessionId: interview.session.id,
        sessionTitle: interview.session.title,
        question: current,
        prompt: current?.prompt || "",
        isCompleted: !current,
        answerSessionId: answerDraft?.answer_session_id || "",
        answerSessionCreatedAt: answerDraft?.created_at || "",
        answerSegments: answerDraft ? answerDraft.segments.map(segmentViewFromDraft) : [],
        answerText: inputDraft?.answer_text || "",
        legacyAudioFileId: inputDraft?.audio_file_id || "",
        legacyAudioDurationSeconds: inputDraft?.audio_duration_seconds ?? null,
        audioSummary: inputDraft?.audio_summary || "",
      });
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

  persistAnswerSessionDraft() {
    const { projectId, sessionId, question, answerSessionId, answerSegments, answerSessionCreatedAt } = this.data;
    if (!projectId || !question || (!answerSessionId && !answerSegments.length)) {
      return;
    }
    writeMemoirAnswerSessionDraft({
      project_id: projectId,
      session_id: sessionId,
      question_id: question.id,
      answer_session_id: answerSessionId || undefined,
      status: answerSessionId ? "draft" : "local",
      segments: answerSegments.map(draftSegmentFromView),
      created_at: answerSessionCreatedAt || nowIso(),
      synced_at: answerSessionId ? nowIso() : undefined,
    });
  },

  async ensureAnswerSession() {
    if (this.data.answerSessionId) {
      return this.data.answerSessionId;
    }
    const question = this.data.question;
    if (!question) {
      throw new Error("当前没有可回答的问题");
    }
    const answerSession = await createMemoirAnswerSession(question.id);
    writeMemoirAnswerSessionDraftFromRemote(answerSession);
    this.setData({
      answerSessionId: answerSession.id,
      answerSessionCreatedAt: answerSession.created_at,
      answerSegments: answerSession.segments.map(segmentViewFromRemote),
    });
    return answerSession.id;
  },

  async appendRecordedSegment(uploaded, transcriptText, errorMessage = "") {
    this.setData({ syncingSegment: true });
    try {
      const answerSessionId = await this.ensureAnswerSession();
      const hasTranscript = Boolean(transcriptText.trim());
      const status: MemoirAnswerSegmentStatus = hasTranscript
        ? "transcribed"
        : errorMessage
          ? "failed"
          : "uploaded";
      const segment = await appendMemoirAnswerSegment(answerSessionId, {
        audio_file_id: uploaded.audioFile.id,
        duration_seconds: uploaded.durationSeconds,
        transcript_text: hasTranscript ? transcriptText : undefined,
        transcript_edited: hasTranscript ? transcriptText : undefined,
        status,
        error_message: errorMessage || undefined,
      });
      const nextSegments = upsertSegmentView(this.data.answerSegments, segment);
      this.setData({
        answerSegments: nextSegments,
        uploadingAudio: false,
        transcribingAudio: !hasTranscript && !errorMessage,
        audioSummary: hasTranscript ? `已添加一段 ${uploaded.durationText}` : `已保存 ${uploaded.durationText}，正在转写`,
      });
      this.persistAnswerSessionDraft();
      if (hasTranscript || errorMessage) {
        return;
      }

      const job = await createMemoirSegmentTranscriptionJob(segment.id);
      if (job.segment) {
        this.setData({
          answerSegments: upsertSegmentView(this.data.answerSegments, {
            ...job.segment,
            status: job.segment.status === "uploaded" ? "transcribing" : job.segment.status,
          }),
        });
      }
      const processedJob = await processMemoirAsrJob(job.id);
      if (processedJob.segment) {
        this.setData({
          answerSegments: upsertSegmentView(this.data.answerSegments, processedJob.segment),
          audioSummary: processedJob.status === "completed" ? `已添加一段 ${uploaded.durationText}` : "录音已保存，转写失败",
          error: processedJob.status === "failed" ? processedJob.error_message || "转写失败" : this.data.error,
        });
        this.persistAnswerSessionDraft();
      }
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "录音分段保存失败" });
    } finally {
      this.setData({ syncingSegment: false });
    }
  },

  beginSegmentProcessing(message) {
    const count = this.data.processingSegmentCount + 1;
    this.setData({
      processingSegmentCount: count,
      processingSegmentLabel: processingSegmentText(count),
      uploadingAudio: true,
      error: "",
      audioSummary: message,
    });
  },

  endSegmentProcessing() {
    const count = Math.max(0, this.data.processingSegmentCount - 1);
    this.setData({
      processingSegmentCount: count,
      processingSegmentLabel: processingSegmentText(count),
      uploadingAudio: count > 0,
      transcribingAudio: count > 0 ? this.data.transcribingAudio : false,
      syncingSegment: count > 0 ? this.data.syncingSegment : false,
    });
  },

  async syncDirtyAnswerSegments() {
    const nextSegments: QuestionAnswerSegmentView[] = [];
    for (const segment of this.data.answerSegments) {
      if (!segment.dirty || !segment.answerSegmentId) {
        nextSegments.push(segment);
        continue;
      }
      const text = segment.text.trim();
      const updated = await updateMemoirAnswerSegment(segment.answerSegmentId, {
        transcript_edited: text,
        status: text ? "transcribed" : "draft",
      });
      nextSegments.push(segmentViewFromRemote(updated));
    }
    const sorted = sortSegments(nextSegments);
    this.setData({ answerSegments: sorted });
    this.persistAnswerSessionDraft();
    return sorted;
  },

  onAnswerInput(event) {
    this.setData({ answerText: event.detail.value });
    this.persistInputDraft();
  },

  onSegmentInput(event) {
    const localId = event.currentTarget.dataset.localId || "";
    const nextSegments = this.data.answerSegments.map((segment) => {
      if (segment.localId !== localId) {
        return segment;
      }
      return {
        ...segment,
        text: event.detail.value,
        statusText: segmentStatusText(segment.status, true),
        dirty: true,
        updatedAt: nowIso(),
      };
    });
    this.setData({ answerSegments: nextSegments });
    this.persistAnswerSessionDraft();
  },

  startRecording() {
    if (this.data.loading || this.data.isCompleted || this.data.submitting || this.data.recording) {
      return;
    }
    this.setData({ error: "", audioSummary: "" });
    this.persistInputDraft();
    this.persistAnswerSessionDraft();
    this.audioRecordingSession = createQuestionRecorderSession(this);
    startMemoirAudioRecording(this.audioRecordingSession);
  },

  stopRecording() {
    if (!this.data.recording) {
      return;
    }
    stopRecordingTimer();
    stopMemoirAudioRecording();
  },

  async submitAnswer() {
    const question = this.data.question;
    const manualText = this.data.answerText.trim();
    const hasSegmentText = this.data.answerSegments.some((segment) => segmentText(segment));
    if (!question || (!manualText && !hasSegmentText)) {
      wx.showToast({ title: "请先输入回答", icon: "none" });
      return;
    }

    if (isAudioBusy(this.data)) {
      wx.showToast({ title: "录音处理中", icon: "none" });
      return;
    }

    this.setData({ submitting: true, error: "" });
    try {
      const answerSessionId = await this.ensureAnswerSession();
      const syncedSegments = await this.syncDirtyAnswerSegments();
      if (manualText) {
        const segment = await appendMemoirAnswerSegment(answerSessionId, {
          audio_file_id: this.data.legacyAudioFileId || undefined,
          duration_seconds: this.data.legacyAudioDurationSeconds ?? undefined,
          transcript_text: manualText,
          transcript_edited: manualText,
          sort_order: syncedSegments.length ? Math.max(...syncedSegments.map((item) => item.sortOrder)) + 1 : 0,
          status: "transcribed",
        });
        this.setData({
          answerSegments: sortSegments([...syncedSegments, segmentViewFromRemote(segment)]),
          answerText: "",
          legacyAudioFileId: "",
          legacyAudioDurationSeconds: null,
          audioSummary: "",
        });
      }
      const submitted = await submitMemoirAnswerSession(answerSessionId);
      if (!submitted.answer_id) {
        throw new Error("回答提交后缺少 answer_id");
      }
      clearMemoirDraft();
      markMemoirDataSnapshotsStale();
      writeMemoirStore({ question });
      clearMemoirQuestionInputDraft(question.id);
      clearMemoirAnswerSessionDraft(this.data.projectId, question.id);
      this.setData({
        answerText: "",
        answerSessionId: "",
        answerSessionCreatedAt: "",
        answerSegments: [],
      });
      wx.navigateTo({ url: `/pages/review/index?answerId=${submitted.answer_id}` });
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
      if (this.data.projectId) {
        clearMemoirAnswerSessionDraft(this.data.projectId, question.id);
      }
      await this.loadQuestion();
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "跳过失败" });
    } finally {
      this.setData({ submitting: false });
    }
  },
});
