import type { MemoirStoryCard, MemoirSummary } from "../../domain/memoir";
import { ensureLocalDevSession } from "../../services/auth";
import { loadMemoirStories, loadMemoirSummary } from "../../services/memoir-dashboard";
import { getCurrentSession } from "../../services/memoir";
import {
  readMemoirDashboardSnapshot,
  writeMemoirDashboardSnapshot,
} from "../../stores/memoir-cache";

interface StoriesData {
  loading: boolean;
  refreshing: boolean;
  hasDashboardData: boolean;
  hasStoryData: boolean;
  error: string;
  weeklyStoryCount: number;
  pendingReviewCount: number;
  confirmedStoryCount: number;
  progressPercent: number;
  stories: MemoirStoryCard[];
}

Page<StoriesData, {
  onLoad(): Promise<void>;
  onShow(): void;
  hydrateFromCache(): void;
  applySnapshot(summary: MemoirSummary, stories: MemoirStoryCard[], hasStoryData: boolean): void;
  loadStoriesPage(): Promise<void>;
  continueInterview(): void;
  backWorkbench(): void;
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
    stories: [],
  },

  async onLoad() {
    this.hydrateFromCache();
    await this.loadStoriesPage();
  },

  onShow() {
    this.hydrateFromCache();
    if (!this.data.refreshing) {
      void this.loadStoriesPage();
    }
  },

  hydrateFromCache() {
    const snapshot = readMemoirDashboardSnapshot();
    if (!snapshot.summary) {
      return;
    }
    this.applySnapshot(snapshot.summary, snapshot.stories, snapshot.hasStories);
  },

  applySnapshot(summary, stories, hasStoryData) {
    this.setData({
      weeklyStoryCount: summary.weekly_story_count,
      pendingReviewCount: summary.pending_review_count,
      confirmedStoryCount: summary.confirmed_story_count,
      progressPercent: summary.progress_percent,
      stories,
      hasDashboardData: true,
      hasStoryData,
    });
  },

  async loadStoriesPage() {
    if (this.data.refreshing) {
      return;
    }
    const loading = !this.data.hasDashboardData;
    this.setData({ loading, refreshing: true, error: "" });
    try {
      await ensureLocalDevSession();
      const interview = await getCurrentSession();
      const summary = await loadMemoirSummary(interview);
      const stories = await loadMemoirStories();
      writeMemoirDashboardSnapshot({ interview, summary, stories });
      this.applySnapshot(summary, stories, true);
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "故事卡加载失败" });
    } finally {
      this.setData({ loading: false, refreshing: false });
    }
  },

  continueInterview() {
    wx.navigateTo({ url: "/pages/question/index" });
  },

  backWorkbench() {
    wx.redirectTo({ url: "/pages/workbench/index" });
  },

  openHome() {
    wx.redirectTo({ url: "/pages/home/index" });
  },

  openWorkbench() {
    wx.redirectTo({ url: "/pages/workbench/index" });
  },

  openFeedback() {
    wx.redirectTo({ url: "/pages/feedback/index" });
  },

  openProfile() {
    wx.redirectTo({ url: "/pages/profile/index" });
  },
});
