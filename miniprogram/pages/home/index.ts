import type {
  MemoirCoverageItem,
  MemoirInterviewState,
  MemoirMemoryStats,
  MemoirOutput,
  MemoirSessionSummary,
  MemoirSummary,
} from "../../domain/memoir";
import { loadMemoirDashboard } from "../../services/memoir-dashboard";
import {
  answeredQuestionCount,
  readMemoirDashboardSnapshot,
} from "../../stores/memoir-cache";

const REVIEW_NAVIGATION_WAIT_MS = 100;
const REVIEW_NAVIGATION_MAX_ATTEMPTS = 600;

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

interface HomeData {
  loading: boolean;
  refreshing: boolean;
  hasDashboardData: boolean;
  error: string;
  projectTitle: string;
  sessionTitle: string;
  currentPrompt: string;
  completedCount: number;
  pendingCount: number;
  weeklyStoryCount: number;
  pendingReviewCount: number;
  confirmedStoryCount: number;
  progressPercent: number;
  latestOutputPreview: string;
  reviewSessionId: string;
}

Page<HomeData, {
  onLoad(): Promise<void>;
  onShow(): void;
  hydrateFromCache(): void;
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
  openPendingReview(): Promise<void>;
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
    currentPrompt: "",
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
    this.hydrateFromCache();
    await this.loadHome();
  },

  onShow() {
    this.hydrateFromCache();
    if (!this.data.refreshing) {
      void this.loadHome();
    }
  },

  hydrateFromCache() {
    const snapshot = readMemoirDashboardSnapshot();
    if (!snapshot.interview || !snapshot.summary) {
      return;
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
      currentPrompt: interview.current_question?.prompt || "本轮访谈已经完成，可以先看看整理好的记忆。",
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
    if (this.data.loading) {
      return;
    }
    if (!this.data.projectTitle) {
      await this.loadHome();
    }
    wx.navigateTo({ url: "/pages/question/index" });
  },

  async openPendingReview() {
    for (
      let attempt = 0;
      this.data.refreshing && !this.data.reviewSessionId && attempt < REVIEW_NAVIGATION_MAX_ATTEMPTS;
      attempt += 1
    ) {
      await wait(REVIEW_NAVIGATION_WAIT_MS);
    }
    if (!this.data.reviewSessionId && !this.data.refreshing) {
      await this.loadHome();
    }
    const sessionId = this.data.reviewSessionId || readMemoirDashboardSnapshot().interview?.session.id || "";
    if (!sessionId) {
      this.setData({ error: this.data.refreshing ? "访谈信息仍在加载，请稍后再试" : "缺少访谈信息" });
      return;
    }
    wx.navigateTo({ url: `/pages/review/index?sessionId=${encodeURIComponent(sessionId)}` });
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
