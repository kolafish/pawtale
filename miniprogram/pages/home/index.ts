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
  type MemoirDashboardSnapshot,
  readMemoirDashboardSnapshot,
  shouldRefreshMemoirDashboardSnapshot,
} from "../../stores/memoir-cache";

interface HomeData {
  loading: boolean;
  refreshing: boolean;
  hasDashboardData: boolean;
  error: string;
  projectId: string;
  projectTitle: string;
  currentPrompt: string;
  primaryActionText: string;
  completedCount: number;
  weeklyStoryCount: number;
  pendingReviewCount: number;
  confirmedStoryCount: number;
  progressPercent: number;
  latestOutputPreview: string;
  latestOutputId: string;
}

const HOME_EMPTY_PROMPT = "我们可以随便聊一聊，小时候、年轻时候，工作，孩子，现在的生活，随便聊点感兴趣的？";

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
  openProjects(): void;
  openStories(): void;
  openWorkbench(): void;
  openHome(): void;
  openProfile(): void;
}>({
  data: {
    loading: false,
    refreshing: false,
    hasDashboardData: false,
    error: "",
    projectId: "",
    projectTitle: "",
    currentPrompt: "",
    primaryActionText: "开始讲述",
    completedCount: 0,
    weeklyStoryCount: 0,
    pendingReviewCount: 0,
    confirmedStoryCount: 0,
    progressPercent: 0,
    latestOutputPreview: "",
    latestOutputId: "",
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
    const completedCount = answeredQuestionCount(interview);
    const latestOutput = outputs[0];
    const latestOutputPreview = latestOutput?.content_preview || sessionSummary?.summary || "";
    const coveragePercent = memoryStats?.coverage_average ?? (coverage.length
      ? Math.round(coverage.reduce((total, item) => total + item.coverage_score, 0) / coverage.length)
      : summary.progress_percent);
    const pendingReviewCount = memoryStats
      ? memoryStats.draft_memory_count + memoryStats.open_question_count
      : summary.pending_review_count;
    this.setData({
      currentPrompt: HOME_EMPTY_PROMPT,
      primaryActionText: completedCount ? "继续讲述" : "开始讲述",
      completedCount,
      weeklyStoryCount: memoryStats?.output_count ?? summary.weekly_story_count,
      pendingReviewCount,
      confirmedStoryCount: memoryStats?.confirmed_memory_count ?? summary.confirmed_story_count,
      progressPercent: coveragePercent,
      latestOutputPreview,
      latestOutputId: latestOutput?.id || sessionSummary?.id || "",
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
    if (this.data.loading) {
      return;
    }
    if (!this.data.projectTitle) {
      await this.loadHome();
    }
    wx.navigateTo({ url: "/pages/voice-start/index" });
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

  openProfile() {
    wx.redirectTo({ url: "/pages/profile/index" });
  },
});
