import type { MemoirStoryCard, MemoirSummary } from "../../domain/memoir";
import { loadMemoirDashboard } from "../../services/memoir-dashboard";
import {
  readMemoirDashboardSnapshot,
} from "../../stores/memoir-cache";

type StoryStatusFilter = "all" | "pending" | "accepted";

interface StoryTapEvent {
  currentTarget: {
    dataset: {
      answerId?: string;
    };
  };
}

interface StoriesData {
  loading: boolean;
  refreshing: boolean;
  hasDashboardData: boolean;
  hasStoryData: boolean;
  error: string;
  totalStoryCount: number;
  weeklyStoryCount: number;
  pendingReviewCount: number;
  confirmedStoryCount: number;
  progressPercent: number;
  activeStatus: StoryStatusFilter;
  stories: MemoirStoryCard[];
  visibleStories: MemoirStoryCard[];
}

function statusFromQuery(status?: string): StoryStatusFilter {
  return status === "pending" || status === "accepted" ? status : "all";
}

function filterStories(stories: MemoirStoryCard[], status: StoryStatusFilter): MemoirStoryCard[] {
  if (status === "pending") {
    return stories.filter((story) => story.draft_status === "draft");
  }
  if (status === "accepted") {
    return stories.filter((story) => story.draft_status === "accepted");
  }
  return stories;
}

Page<StoriesData, {
  onLoad(query?: Record<string, string | undefined>): Promise<void>;
  onShow(): void;
  hydrateFromCache(): void;
  applySnapshot(summary: MemoirSummary, stories: MemoirStoryCard[], hasStoryData: boolean): void;
  loadStoriesPage(): Promise<void>;
  selectAll(): void;
  selectPending(): void;
  selectAccepted(): void;
  setActiveStatus(status: StoryStatusFilter): void;
  openStory(event: StoryTapEvent): void;
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
    totalStoryCount: 0,
    weeklyStoryCount: 0,
    pendingReviewCount: 0,
    confirmedStoryCount: 0,
    progressPercent: 0,
    activeStatus: "all",
    stories: [],
    visibleStories: [],
  },

  async onLoad(query) {
    this.setData({ activeStatus: statusFromQuery(query?.status) });
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
      totalStoryCount: summary.total_story_count,
      weeklyStoryCount: summary.weekly_story_count,
      pendingReviewCount: summary.pending_review_count,
      confirmedStoryCount: summary.confirmed_story_count,
      progressPercent: summary.progress_percent,
      stories,
      visibleStories: filterStories(stories, this.data.activeStatus),
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
      const { summary, stories } = await loadMemoirDashboard({ includeStories: true });
      this.applySnapshot(summary, stories, true);
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "故事卡加载失败" });
    } finally {
      this.setData({ loading: false, refreshing: false });
    }
  },

  selectAll() {
    this.setActiveStatus("all");
  },

  selectPending() {
    this.setActiveStatus("pending");
  },

  selectAccepted() {
    this.setActiveStatus("accepted");
  },

  setActiveStatus(status) {
    this.setData({
      activeStatus: status,
      visibleStories: filterStories(this.data.stories, status),
    });
  },

  openStory(event) {
    const answerId = event.currentTarget.dataset.answerId;
    if (!answerId) {
      return;
    }
    wx.navigateTo({ url: `/pages/review/index?answerId=${answerId}&returnTo=stories` });
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
