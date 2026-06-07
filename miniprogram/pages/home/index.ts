import type {
  MemoirCoverageItem,
  MemoirInterviewState,
  MemoirMemoryStats,
  MemoirOutput,
  MemoirSessionSummary,
  MemoirSummary,
} from "../../domain/memoir";
import { loadMemoirDashboard } from "../../services/memoir-dashboard";
import { skipQuestion } from "../../services/memoir";
import {
  answeredQuestionCount,
  markMemoirDataSnapshotsStale,
  type MemoirDashboardSnapshot,
  readMemoirDashboardSnapshot,
  shouldRefreshMemoirDashboardSnapshot,
} from "../../stores/memoir-cache";
import { clearMemoirQuestionInputDraft } from "../../stores/memoir-input-drafts";
import { writeMemoirStore } from "../../stores/session";

interface TouchPoint {
  clientX: number;
  clientY: number;
}

interface TouchEvent {
  touches?: TouchPoint[];
  changedTouches?: TouchPoint[];
}

interface HomeData {
  loading: boolean;
  refreshing: boolean;
  hasDashboardData: boolean;
  error: string;
  projectTitle: string;
  sessionTitle: string;
  currentQuestionId: string;
  currentPrompt: string;
  questionCardTranslateX: number;
  questionCardRotate: number;
  questionCardDragging: boolean;
  questionCardLeaving: boolean;
  questionSwipeReady: boolean;
  skippingQuestion: boolean;
  completedCount: number;
  pendingCount: number;
  weeklyStoryCount: number;
  pendingReviewCount: number;
  confirmedStoryCount: number;
  progressPercent: number;
  latestOutputPreview: string;
  reviewSessionId: string;
}

const QUESTION_SWIPE_THRESHOLD_PX = 90;
const QUESTION_SWIPE_MAX_TRANSLATE_PX = 160;
const QUESTION_SWIPE_EXIT_TRANSLATE_PX = 420;
const QUESTION_SWIPE_MAX_ROTATE_DEG = 8;
const QUESTION_VERTICAL_CANCEL_PX = 18;

let questionSwipeStartX = 0;
let questionSwipeStartY = 0;
let questionSwipeDeltaX = 0;
let questionSwipeTracking = false;

function firstTouch(points?: TouchPoint[]): TouchPoint | null {
  return points && points.length ? points[0] : null;
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

Page<HomeData, {
  onLoad(): Promise<void>;
  onShow(): void;
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
  resetQuestionSwipe(): void;
  skipCurrentQuestionBySwipe(direction: number): Promise<void>;
  openPendingReview(): Promise<void>;
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
    projectTitle: "",
    sessionTitle: "",
    currentQuestionId: "",
    currentPrompt: "",
    questionCardTranslateX: 0,
    questionCardRotate: 0,
    questionCardDragging: false,
    questionCardLeaving: false,
    questionSwipeReady: false,
    skippingQuestion: false,
    completedCount: 0,
    pendingCount: 0,
    weeklyStoryCount: 0,
    pendingReviewCount: 0,
    confirmedStoryCount: 0,
    progressPercent: 0,
    latestOutputPreview: "",
    reviewSessionId: "",
  },

  async onLoad() {
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

  hydrateFromCache() {
    const snapshot = readMemoirDashboardSnapshot();
    if (!snapshot.interview || !snapshot.summary) {
      return snapshot;
    }
    this.setData({
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
    const coveragePercent = memoryStats?.coverage_average ?? (coverage.length
      ? Math.round(coverage.reduce((total, item) => total + item.coverage_score, 0) / coverage.length)
      : summary.progress_percent);
    const pendingReviewCount = memoryStats
      ? memoryStats.draft_memory_count + memoryStats.open_question_count
      : summary.pending_review_count;
    this.setData({
      sessionTitle: interview.session.title,
      reviewSessionId: interview.session.id,
      currentQuestionId: interview.current_question?.id || "",
      currentPrompt: interview.current_question?.prompt || "本轮访谈已经完成，可以先看看整理好的记忆。",
      questionCardTranslateX: 0,
      questionCardRotate: 0,
      questionCardDragging: false,
      questionCardLeaving: false,
      questionSwipeReady: false,
      completedCount: Math.max(summary.completed_question_count, answeredQuestionCount(interview)),
      pendingCount: interview.pending_question_count,
      weeklyStoryCount: memoryStats?.output_count ?? summary.weekly_story_count,
      pendingReviewCount,
      confirmedStoryCount: memoryStats?.confirmed_memory_count ?? summary.confirmed_story_count,
      progressPercent: coveragePercent,
      latestOutputPreview: outputs[0]?.content_preview || sessionSummary?.summary || "",
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
      } = await loadMemoirDashboard({ includeProject: true, includeMemory: true });
      this.setData({
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
    if (this.data.loading || this.data.skippingQuestion) {
      return;
    }
    if (!this.data.projectTitle) {
      await this.loadHome();
    }
    wx.navigateTo({ url: "/pages/question/index" });
  },

  onQuestionTouchStart(event) {
    if (!this.data.currentQuestionId || this.data.loading || this.data.refreshing || this.data.skippingQuestion) {
      return;
    }
    const touch = firstTouch(event.touches);
    if (!touch) {
      return;
    }
    questionSwipeStartX = touch.clientX;
    questionSwipeStartY = touch.clientY;
    questionSwipeDeltaX = 0;
    questionSwipeTracking = true;
  },

  onQuestionTouchMove(event) {
    if (!questionSwipeTracking || !this.data.currentQuestionId || this.data.skippingQuestion) {
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
    const translateX = cardTranslate(deltaX);
    this.setData({
      questionCardTranslateX: translateX,
      questionCardRotate: cardRotate(translateX),
      questionCardDragging: true,
      questionCardLeaving: false,
      questionSwipeReady: Math.abs(deltaX) >= QUESTION_SWIPE_THRESHOLD_PX,
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
      && !this.data.skippingQuestion
    ) {
      await this.skipCurrentQuestionBySwipe(questionSwipeDeltaX >= 0 ? 1 : -1);
      return;
    }
    this.resetQuestionSwipe();
  },

  onQuestionTouchCancel() {
    this.resetQuestionSwipe();
  },

  resetQuestionSwipe() {
    questionSwipeDeltaX = 0;
    questionSwipeTracking = false;
    this.setData({
      questionCardTranslateX: 0,
      questionCardRotate: 0,
      questionCardDragging: false,
      questionCardLeaving: false,
      questionSwipeReady: false,
    });
  },

  async skipCurrentQuestionBySwipe(direction) {
    const questionId = this.data.currentQuestionId;
    if (!questionId || this.data.skippingQuestion) {
      this.resetQuestionSwipe();
      return;
    }

    this.setData({
      skippingQuestion: true,
      error: "",
      questionCardDragging: false,
      questionCardLeaving: true,
      questionCardTranslateX: direction > 0 ? QUESTION_SWIPE_EXIT_TRANSLATE_PX : -QUESTION_SWIPE_EXIT_TRANSLATE_PX,
      questionCardRotate: direction > 0 ? 12 : -12,
      questionSwipeReady: false,
    });
    try {
      const interview = await skipQuestion(questionId);
      markMemoirDataSnapshotsStale();
      writeMemoirStore({ interview });
      clearMemoirQuestionInputDraft(questionId);
      const snapshot = readMemoirDashboardSnapshot();
      if (snapshot.summary) {
        this.applySnapshot(
          interview,
          snapshot.summary,
          snapshot.coverage,
          snapshot.outputs,
          snapshot.memoryStats,
          snapshot.sessionSummary,
        );
      }
      await this.loadHome();
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "换题失败" });
    } finally {
      this.setData({ skippingQuestion: false });
      this.resetQuestionSwipe();
    }
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
