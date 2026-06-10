import type {
  MemoirCoverageItem,
  MemoirInterviewState,
  MemoirMemoryStats,
  MemoirOutput,
  MemoirQuestion,
  MemoirSessionSummary,
  MemoirSummary,
  MemoirTtsSourceType,
} from "../../domain/memoir";
import { loadMemoirDashboard } from "../../services/memoir-dashboard";
import {
  answeredQuestionCount,
  type MemoirDashboardSnapshot,
  readMemoirDashboardSnapshot,
  shouldRefreshMemoirDashboardSnapshot,
} from "../../stores/memoir-cache";
import {
  readMemoirAnswerSessionDraft,
  type MemoirAnswerSessionDraft,
} from "../../stores/memoir-answer-session-drafts";
import { writeMemoirStore } from "../../stores/session";
import {
  memoirTtsSourceKey,
  pauseMemoirTtsPlayback,
  playMemoirTtsPlayback,
  readMemoirTtsPlaybackState,
  subscribeMemoirTtsPlayback,
} from "../../services/memoir-tts-player";
import { memoirTtsViewPatch } from "../../services/memoir-tts-view";

interface TouchPoint {
  clientX: number;
  clientY: number;
}

interface TouchEvent {
  touches?: TouchPoint[];
  changedTouches?: TouchPoint[];
}

interface TtsSourceEvent {
  currentTarget: {
    dataset: {
      sourceType?: MemoirTtsSourceType;
      sourceId?: string;
      label?: string;
    };
  };
}

interface HomeData {
  loading: boolean;
  refreshing: boolean;
  hasDashboardData: boolean;
  error: string;
  projectId: string;
  projectTitle: string;
  sessionTitle: string;
  currentQuestionId: string;
  currentQuestionTtsSourceKey: string;
  currentPrompt: string;
  hasAnswerDraft: boolean;
  answerDraftLabel: string;
  answerDraftPendingLabel: string;
  primaryActionText: string;
  questionCardTranslateX: number;
  questionCardRotate: number;
  questionCardDragging: boolean;
  questionCardLeaving: boolean;
  questionSwipeReady: boolean;
  switchingQuestion: boolean;
  canSwitchQuestion: boolean;
  completedCount: number;
  weeklyStoryCount: number;
  pendingReviewCount: number;
  confirmedStoryCount: number;
  progressPercent: number;
  latestOutputPreview: string;
  latestOutputId: string;
  latestOutputTtsSourceKey: string;
  latestOutputTtsSourceType: MemoirTtsSourceType | "";
  reviewSessionId: string;
  ttsStatus: string;
  ttsSourceKey: string;
  ttsMessage: string;
  ttsError: string;
  ttsPlaying: boolean;
  ttsBusy: boolean;
}

const HOME_EMPTY_PROMPT = "我们可以随便聊一聊，小时候、年轻时候，工作，孩子，现在的生活，随便聊点感兴趣的？";
const QUESTION_SWIPE_THRESHOLD_PX = 90;
const QUESTION_SWIPE_MAX_TRANSLATE_PX = 160;
const QUESTION_SWIPE_EXIT_TRANSLATE_PX = 420;
const QUESTION_SWIPE_MAX_ROTATE_DEG = 8;
const QUESTION_VERTICAL_CANCEL_PX = 18;
const QUESTION_SWIPE_SWITCH_DELAY_MS = 170;

let questionSwipeStartX = 0;
let questionSwipeStartY = 0;
let questionSwipeDeltaX = 0;
let questionSwipeTracking = false;
let questionSwipeSwitchTimer: ReturnType<typeof setTimeout> | null = null;
let questionSwipeLastTranslateX = 0;
let questionSwipeLastReady = false;

function firstTouch(points?: TouchPoint[]): TouchPoint | null {
  return points && points.length ? points[0] : null;
}

function clearQuestionSwipeSwitchTimer(): void {
  if (questionSwipeSwitchTimer === null) {
    return;
  }
  clearTimeout(questionSwipeSwitchTimer);
  questionSwipeSwitchTimer = null;
}

function resetSwipeTracking(): void {
  questionSwipeDeltaX = 0;
  questionSwipeTracking = false;
  questionSwipeLastTranslateX = 0;
  questionSwipeLastReady = false;
}

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

function cardTranslate(deltaX: number): number {
  return clamp(deltaX, -QUESTION_SWIPE_MAX_TRANSLATE_PX, QUESTION_SWIPE_MAX_TRANSLATE_PX);
}

function cardRotate(translateX: number): number {
  return Math.round((translateX / QUESTION_SWIPE_MAX_TRANSLATE_PX) * QUESTION_SWIPE_MAX_ROTATE_DEG);
}

function promptForQuestion(question?: MemoirQuestion | null): string {
  return question?.prompt || HOME_EMPTY_PROMPT;
}

function answerDraftForQuestion(projectId: string, question?: MemoirQuestion | null): MemoirAnswerSessionDraft | undefined {
  return projectId && question ? readMemoirAnswerSessionDraft(projectId, question.id) : undefined;
}

function answerDraftLabel(draft?: MemoirAnswerSessionDraft): string {
  if (!draft) {
    return "";
  }
  const segmentText = draft.segments.length ? `${draft.segments.length} 段` : "未提交";
  return draft.merged_text ? `已暂存 ${segmentText}` : `已创建 ${segmentText}`;
}

function answerDraftPendingLabel(draft?: MemoirAnswerSessionDraft): string {
  if (!draft?.pending_upload_count) {
    return "";
  }
  return `${draft.pending_upload_count} 段待上传`;
}

function answerDraftState(projectId: string, question?: MemoirQuestion | null): Pick<
  HomeData,
  "hasAnswerDraft" | "answerDraftLabel" | "answerDraftPendingLabel" | "primaryActionText"
> {
  const draft = answerDraftForQuestion(projectId, question);
  const hasAnswerDraft = Boolean(draft && (draft.answer_session_id || draft.segments.length || draft.merged_text));
  return {
    hasAnswerDraft,
    answerDraftLabel: answerDraftLabel(draft),
    answerDraftPendingLabel: answerDraftPendingLabel(draft),
    primaryActionText: hasAnswerDraft ? "继续讲述" : "开始讲述",
  };
}

function nextPendingQuestionAfter(interview: MemoirInterviewState | undefined, questionId: string): MemoirQuestion | null {
  if (!interview) {
    return null;
  }
  const orderedQuestions = [...interview.questions].sort((left, right) => left.sort_order - right.sort_order);
  const currentIndex = orderedQuestions.findIndex((question) => question.id === questionId);
  const pendingQuestions = orderedQuestions.filter((question) => (
    question.id !== questionId
    && question.status === "pending"
  ));
  if (currentIndex === -1) {
    return pendingQuestions[0] || null;
  }
  return pendingQuestions.find((question) => question.sort_order > orderedQuestions[currentIndex].sort_order)
    || pendingQuestions[0]
    || null;
}

Page<HomeData, {
  unsubscribeTtsPlayback?: () => void;
  onLoad(): Promise<void>;
  onShow(): void;
  onUnload(): void;
  registerTtsPlayback(): void;
  hydrateFromCache(): MemoirDashboardSnapshot;
  applySnapshot(
    interview: MemoirInterviewState,
    summary: MemoirSummary,
    coverage?: MemoirCoverageItem[],
    outputs?: MemoirOutput[],
    memoryStats?: MemoirMemoryStats,
    sessionSummary?: MemoirSessionSummary | null,
  ): void;
  loadHome(): Promise<void>;
  continueInterview(): Promise<void>;
  onQuestionTouchStart(event: TouchEvent): void;
  onQuestionTouchMove(event: TouchEvent): void;
  onQuestionTouchEnd(): Promise<void>;
  onQuestionTouchCancel(): void;
  showQuestionCard(question?: MemoirQuestion | null): void;
  resetQuestionSwipe(): void;
  switchQuestionManually(): void;
  switchQuestionBySwipe(direction: number): void;
  openPendingReview(): Promise<void>;
  toggleTtsSource(event: TtsSourceEvent): Promise<void>;
  openProjects(): void;
  openStories(): void;
  openWorkbench(): void;
  openHome(): void;
  openQa(): void;
  openProfile(): void;
}>({
  data: {
    loading: false,
    refreshing: false,
    hasDashboardData: false,
    error: "",
    projectId: "",
    projectTitle: "",
    sessionTitle: "",
    currentQuestionId: "",
    currentQuestionTtsSourceKey: "",
    currentPrompt: "",
    hasAnswerDraft: false,
    answerDraftLabel: "",
    answerDraftPendingLabel: "",
    primaryActionText: "开始讲述",
    questionCardTranslateX: 0,
    questionCardRotate: 0,
    questionCardDragging: false,
    questionCardLeaving: false,
    questionSwipeReady: false,
    switchingQuestion: false,
    canSwitchQuestion: false,
    completedCount: 0,
    weeklyStoryCount: 0,
    pendingReviewCount: 0,
    confirmedStoryCount: 0,
    progressPercent: 0,
    latestOutputPreview: "",
    latestOutputId: "",
    latestOutputTtsSourceKey: "",
    latestOutputTtsSourceType: "",
    reviewSessionId: "",
    ...memoirTtsViewPatch(readMemoirTtsPlaybackState()),
  },

  async onLoad() {
    this.registerTtsPlayback();
    const snapshot = this.hydrateFromCache();
    if (shouldRefreshMemoirDashboardSnapshot(snapshot)) {
      await this.loadHome();
    }
  },

  onShow() {
    const snapshot = this.hydrateFromCache();
    if (!this.data.refreshing && shouldRefreshMemoirDashboardSnapshot(snapshot)) {
      void this.loadHome();
    }
  },

  onUnload() {
    if (this.unsubscribeTtsPlayback) {
      this.unsubscribeTtsPlayback();
      this.unsubscribeTtsPlayback = undefined;
    }
  },

  registerTtsPlayback() {
    if (this.unsubscribeTtsPlayback) {
      return;
    }
    this.unsubscribeTtsPlayback = subscribeMemoirTtsPlayback((playbackState) => {
      this.setData(memoirTtsViewPatch(playbackState));
    });
  },

  hydrateFromCache() {
    const snapshot = readMemoirDashboardSnapshot();
    if (!snapshot.interview || !snapshot.summary) {
      return snapshot;
    }
    this.setData({
      projectId: snapshot.project?.id || this.data.projectId,
      projectTitle: snapshot.project?.title || this.data.projectTitle,
    });
    this.applySnapshot(
      snapshot.interview,
      snapshot.summary,
      snapshot.coverage,
      snapshot.outputs,
      snapshot.memoryStats,
      snapshot.sessionSummary,
    );
    return snapshot;
  },

  applySnapshot(interview, summary, coverage = [], outputs = [], memoryStats, sessionSummary = null) {
    clearQuestionSwipeSwitchTimer();
    const currentQuestion = interview.current_question;
    const draftState = answerDraftState(this.data.projectId, currentQuestion);
    const latestOutput = outputs[0];
    const latestOutputPreview = latestOutput?.content_preview || sessionSummary?.summary || "";
    const latestOutputTtsSourceType: MemoirTtsSourceType | "" = latestOutput?.content_preview
      ? "output"
      : sessionSummary?.id && sessionSummary.summary
        ? "session_summary"
        : "";
    const latestOutputId = latestOutputTtsSourceType === "output" ? latestOutput?.id || "" : sessionSummary?.id || "";
    const coveragePercent = memoryStats?.coverage_average ?? (coverage.length
      ? Math.round(coverage.reduce((total, item) => total + item.coverage_score, 0) / coverage.length)
      : summary.progress_percent);
    const pendingReviewCount = memoryStats
      ? memoryStats.draft_memory_count + memoryStats.open_question_count
      : summary.pending_review_count;
    this.setData({
      sessionTitle: interview.session.title,
      reviewSessionId: interview.session.id,
      currentQuestionId: currentQuestion?.id || "",
      currentQuestionTtsSourceKey: currentQuestion
        ? memoirTtsSourceKey({ source_type: "question", source_id: currentQuestion.id })
        : "",
      currentPrompt: promptForQuestion(currentQuestion),
      ...draftState,
      questionCardTranslateX: 0,
      questionCardRotate: 0,
      questionCardDragging: false,
      questionCardLeaving: false,
      questionSwipeReady: false,
      canSwitchQuestion: Boolean(currentQuestion && nextPendingQuestionAfter(interview, currentQuestion.id)),
      completedCount: answeredQuestionCount(interview),
      weeklyStoryCount: memoryStats?.output_count ?? summary.weekly_story_count,
      pendingReviewCount,
      confirmedStoryCount: memoryStats?.confirmed_memory_count ?? summary.confirmed_story_count,
      progressPercent: coveragePercent,
      latestOutputPreview,
      latestOutputId,
      latestOutputTtsSourceKey: latestOutputId && latestOutputTtsSourceType
        ? memoirTtsSourceKey({ source_type: latestOutputTtsSourceType, source_id: latestOutputId })
        : "",
      latestOutputTtsSourceType,
      hasDashboardData: true,
    });
  },

  async loadHome() {
    if (this.data.refreshing) {
      return;
    }
    const loading = !this.data.hasDashboardData;
    this.setData({ loading, refreshing: true, error: "" });
    try {
      const {
        project,
        interview,
        summary,
        coverage,
        outputs,
        memoryStats,
        sessionSummary,
      } = await loadMemoirDashboard();
      this.setData({
        projectId: project?.id || "",
        projectTitle: project?.title || "",
      });
      this.applySnapshot(interview, summary, coverage, outputs, memoryStats, sessionSummary);
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "加载失败" });
    } finally {
      this.setData({ loading: false, refreshing: false });
    }
  },

  async continueInterview() {
    if (this.data.loading || this.data.switchingQuestion) {
      return;
    }
    if (!this.data.projectTitle) {
      await this.loadHome();
    }
    const questionId = this.data.currentQuestionId;
    wx.navigateTo({
      url: questionId
        ? `/pages/question/index?questionId=${encodeURIComponent(questionId)}`
        : "/pages/question/index",
    });
  },

  onQuestionTouchStart(event) {
    if (!this.data.currentQuestionId || this.data.loading || this.data.refreshing || this.data.switchingQuestion) {
      return;
    }
    const touch = firstTouch(event.touches);
    if (!touch) {
      return;
    }
    questionSwipeStartX = touch.clientX;
    questionSwipeStartY = touch.clientY;
    resetSwipeTracking();
    questionSwipeTracking = true;
  },

  onQuestionTouchMove(event) {
    if (!questionSwipeTracking || !this.data.currentQuestionId || this.data.switchingQuestion) {
      return;
    }
    const touch = firstTouch(event.touches);
    if (!touch) {
      return;
    }
    const deltaX = touch.clientX - questionSwipeStartX;
    const deltaY = touch.clientY - questionSwipeStartY;
    if (Math.abs(deltaY) > QUESTION_VERTICAL_CANCEL_PX && Math.abs(deltaY) > Math.abs(deltaX)) {
      this.resetQuestionSwipe();
      return;
    }

    questionSwipeDeltaX = deltaX;
    const translateX = Math.round(cardTranslate(deltaX));
    const swipeReady = Math.abs(deltaX) >= QUESTION_SWIPE_THRESHOLD_PX;
    if (Math.abs(translateX - questionSwipeLastTranslateX) < 2 && swipeReady === questionSwipeLastReady) {
      return;
    }
    questionSwipeLastTranslateX = translateX;
    questionSwipeLastReady = swipeReady;
    this.setData({
      questionCardTranslateX: translateX,
      questionCardRotate: cardRotate(translateX),
      questionCardDragging: true,
      questionCardLeaving: false,
      questionSwipeReady: swipeReady,
    });
  },

  async onQuestionTouchEnd() {
    if (!questionSwipeTracking) {
      return;
    }
    questionSwipeTracking = false;
    if (
      Math.abs(questionSwipeDeltaX) >= QUESTION_SWIPE_THRESHOLD_PX
      && this.data.currentQuestionId
      && !this.data.loading
      && !this.data.refreshing
      && !this.data.switchingQuestion
    ) {
      this.switchQuestionBySwipe(questionSwipeDeltaX >= 0 ? 1 : -1);
      return;
    }
    this.resetQuestionSwipe();
  },

  onQuestionTouchCancel() {
    this.resetQuestionSwipe();
  },

  showQuestionCard(question) {
    clearQuestionSwipeSwitchTimer();
    resetSwipeTracking();
    const snapshot = readMemoirDashboardSnapshot();
    if (snapshot.interview) {
      const questions = question && !snapshot.interview.questions.some((item) => item.id === question.id)
        ? [...snapshot.interview.questions, question]
        : snapshot.interview.questions;
      writeMemoirStore({
        interview: {
          ...snapshot.interview,
          session: {
            ...snapshot.interview.session,
            current_question_id: question?.id,
            status: question ? "active" : snapshot.interview.session.status,
          },
          current_question: question || undefined,
          questions,
        },
      });
    }
    const projectId = snapshot.project?.id || this.data.projectId;
    const draftState = answerDraftState(projectId, question);
    const canSwitchQuestion = Boolean(question && snapshot.interview && nextPendingQuestionAfter(snapshot.interview, question.id));
    this.setData({
      projectId,
      currentQuestionId: question?.id || "",
      currentQuestionTtsSourceKey: question
        ? memoirTtsSourceKey({ source_type: "question", source_id: question.id })
        : "",
      currentPrompt: promptForQuestion(question),
      ...draftState,
      questionCardTranslateX: 0,
      questionCardRotate: 0,
      questionCardDragging: false,
      questionCardLeaving: false,
      questionSwipeReady: false,
      switchingQuestion: false,
      canSwitchQuestion,
    });
  },

  resetQuestionSwipe() {
    resetSwipeTracking();
    this.setData({
      questionCardTranslateX: 0,
      questionCardRotate: 0,
      questionCardDragging: false,
      questionCardLeaving: false,
      questionSwipeReady: false,
    });
  },

  switchQuestionManually() {
    if (!this.data.canSwitchQuestion || this.data.loading || this.data.refreshing || this.data.switchingQuestion) {
      return;
    }
    this.switchQuestionBySwipe(1);
  },

  switchQuestionBySwipe(direction) {
    const questionId = this.data.currentQuestionId;
    if (!questionId || this.data.switchingQuestion) {
      this.resetQuestionSwipe();
      return;
    }

    const snapshotBeforeSkip = readMemoirDashboardSnapshot();
    const nextQuestion = nextPendingQuestionAfter(snapshotBeforeSkip.interview, questionId);
    if (!nextQuestion) {
      this.resetQuestionSwipe();
      return;
    }

    clearQuestionSwipeSwitchTimer();
    this.setData({
      switchingQuestion: true,
      error: "",
      questionCardDragging: false,
      questionCardLeaving: true,
      questionCardTranslateX: direction > 0 ? QUESTION_SWIPE_EXIT_TRANSLATE_PX : -QUESTION_SWIPE_EXIT_TRANSLATE_PX,
      questionCardRotate: direction > 0 ? 12 : -12,
      questionSwipeReady: false,
    });
    questionSwipeSwitchTimer = setTimeout(() => {
      questionSwipeSwitchTimer = null;
      if (this.data.switchingQuestion && this.data.currentQuestionId === questionId) {
        this.showQuestionCard(nextQuestion);
      }
    }, QUESTION_SWIPE_SWITCH_DELAY_MS);
  },

  async openPendingReview() {
    const snapshot = readMemoirDashboardSnapshot();
    const sessionId = this.data.reviewSessionId || snapshot.interview?.session.id || "";
    if (sessionId) {
      wx.navigateTo({ url: `/pages/review/index?sessionId=${encodeURIComponent(sessionId)}` });
      return;
    }
    if (!this.data.refreshing) {
      await this.loadHome();
    }
    const fallbackSessionId = this.data.reviewSessionId || readMemoirDashboardSnapshot().interview?.session.id || "";
    if (!fallbackSessionId) {
      this.setData({ error: "缺少访谈信息" });
      return;
    }
    wx.navigateTo({ url: `/pages/review/index?sessionId=${encodeURIComponent(fallbackSessionId)}` });
  },

  async toggleTtsSource(event) {
    const sourceType = event.currentTarget.dataset.sourceType;
    const sourceId = event.currentTarget.dataset.sourceId || "";
    if (!sourceType || !sourceId || this.data.ttsBusy) {
      return;
    }
    const sourceKey = memoirTtsSourceKey({ source_type: sourceType, source_id: sourceId });
    if (this.data.ttsSourceKey === sourceKey && this.data.ttsPlaying) {
      pauseMemoirTtsPlayback();
      return;
    }
    await playMemoirTtsPlayback({
      source_type: sourceType,
      source_id: sourceId,
      label: event.currentTarget.dataset.label || "",
    });
  },

  openProjects() {
    wx.navigateTo({ url: "/pages/projects/index" });
  },

  openStories() {
    wx.navigateTo({ url: "/pages/stories/index" });
  },

  openWorkbench() {
    wx.redirectTo({ url: "/pages/workbench/index" });
  },

  openHome() {
  },

  openQa() {
    wx.redirectTo({ url: "/pages/qa/index" });
  },

  openProfile() {
    wx.redirectTo({ url: "/pages/profile/index" });
  },
});
