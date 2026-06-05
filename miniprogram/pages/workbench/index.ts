import type { MemoirInterviewState, MemoirStoryCard, MemoirSummary } from "../../domain/memoir";
import { loadMemoirDashboard } from "../../services/memoir-dashboard";
import {
  readMemoirDashboardSnapshot,
} from "../../stores/memoir-cache";

interface WorkbenchData {
  loading: boolean;
  refreshing: boolean;
  hasDashboardData: boolean;
  hasStoryData: boolean;
  error: string;
  weeklyStoryCount: number;
  pendingReviewCount: number;
  confirmedStoryCount: number;
  progressPercent: number;
  pendingCount: number;
  stories: MemoirStoryCard[];
}

Page<WorkbenchData, {
  onLoad(): Promise<void>;
  onShow(): void;
  hydrateFromCache(): void;
  applySnapshot(
    interview: MemoirInterviewState,
    summary: MemoirSummary,
    stories: MemoirStoryCard[],
    hasStoryData: boolean,
  ): void;
  loadWorkbench(): Promise<void>;
  continueInterview(): void;
  openStories(): void;
  backHome(): void;
  openHome(): void;
  openWorkbench(): void;
  openFeedback(): void;
  openProfile(): void;
}>({
  data: {
    loading: false,
    refreshing: false,
    hasDashboardData: false,
    hasStoryData: false,
    error: "",
    weeklyStoryCount: 0,
    pendingReviewCount: 0,
    confirmedStoryCount: 0,
    progressPercent: 0,
    pendingCount: 0,
    stories: [],
  },

  async onLoad() {
    this.hydrateFromCache();
    await this.loadWorkbench();
  },

  onShow() {
    this.hydrateFromCache();
    if (!this.data.refreshing) {
      void this.loadWorkbench();
    }
  },

  hydrateFromCache() {
    const snapshot = readMemoirDashboardSnapshot();
    if (!snapshot.interview || !snapshot.summary) {
      return;
    }
    this.applySnapshot(snapshot.interview, snapshot.summary, snapshot.stories, snapshot.hasStories);
  },

  applySnapshot(interview, summary, stories, hasStoryData) {
    this.setData({
      weeklyStoryCount: summary.weekly_story_count,
      pendingReviewCount: summary.pending_review_count,
      confirmedStoryCount: summary.confirmed_story_count,
      progressPercent: summary.progress_percent,
      pendingCount: interview.pending_question_count,
      stories,
      hasDashboardData: true,
      hasStoryData,
    });
  },

  async loadWorkbench() {
    if (this.data.refreshing) {
      return;
    }
    const loading = !this.data.hasDashboardData;
    this.setData({ loading, refreshing: true, error: "" });
    try {
      const { interview, summary, stories } = await loadMemoirDashboard({ includeStories: true });
      this.applySnapshot(interview, summary, stories, true);
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "工作台加载失败" });
    } finally {
      this.setData({ loading: false, refreshing: false });
    }
  },

  continueInterview() {
    wx.navigateTo({ url: "/pages/question/index" });
  },

  openStories() {
    wx.navigateTo({ url: "/pages/stories/index" });
  },

  backHome() {
    wx.redirectTo({ url: "/pages/home/index" });
  },

  openHome() {
    wx.redirectTo({ url: "/pages/home/index" });
  },

  openWorkbench() {
  },

  openFeedback() {
    wx.redirectTo({ url: "/pages/feedback/index" });
  },

  openProfile() {
    wx.redirectTo({ url: "/pages/profile/index" });
  },
});
