import type { MemoirInterviewState, MemoirSummary } from "../../domain/memoir";
import { loadMemoirDashboard } from "../../services/memoir-dashboard";
import {
  answeredQuestionCount,
  readMemoirDashboardSnapshot,
} from "../../stores/memoir-cache";

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
  latestDraftPreview: string;
}

Page<HomeData, {
  onLoad(): Promise<void>;
  onShow(): void;
  hydrateFromCache(): void;
  applySnapshot(interview: MemoirInterviewState, summary: MemoirSummary): void;
  loadHome(): Promise<void>;
  continueInterview(): Promise<void>;
  openStories(): void;
  openWorkbench(): void;
  openHome(): void;
  openFeedback(): void;
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
    latestDraftPreview: "",
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
    this.applySnapshot(snapshot.interview, snapshot.summary);
  },

  applySnapshot(interview, summary) {
    this.setData({
      sessionTitle: interview.session.title,
      currentPrompt: interview.current_question?.prompt || "本轮访谈已经完成，可以先看看整理好的故事。",
      completedCount: Math.max(summary.completed_question_count, answeredQuestionCount(interview)),
      pendingCount: interview.pending_question_count,
      weeklyStoryCount: summary.weekly_story_count,
      pendingReviewCount: summary.pending_review_count,
      confirmedStoryCount: summary.confirmed_story_count,
      progressPercent: summary.progress_percent,
      latestDraftPreview: interview.latest_draft?.content_edited || "",
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
      const { project, interview, summary } = await loadMemoirDashboard({ includeProject: true });
      this.setData({
        projectTitle: project?.title || "",
      });
      this.applySnapshot(interview, summary);
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

  openStories() {
    wx.navigateTo({ url: "/pages/stories/index" });
  },

  openWorkbench() {
    wx.redirectTo({ url: "/pages/workbench/index" });
  },

  openHome() {
  },

  openFeedback() {
    wx.redirectTo({ url: "/pages/feedback/index" });
  },

  openProfile() {
    wx.redirectTo({ url: "/pages/profile/index" });
  },
});
