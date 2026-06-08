import type { MemoirMemoryStats, MemoirOutput } from "../../domain/memoir";
import { loadMemoirDashboard } from "../../services/memoir-dashboard";
import {
  type MemoirDashboardSnapshot,
  readMemoirDashboardSnapshot,
  shouldRefreshMemoirDashboardSnapshot,
} from "../../stores/memoir-cache";

type OutputFilter = "all" | "story_fragment";

interface StoriesData {
  loading: boolean;
  refreshing: boolean;
  hasDashboardData: boolean;
  hasOutputData: boolean;
  error: string;
  outputCount: number;
  storyFragmentCount: number;
  pendingReviewCount: number;
  progressPercent: number;
  activeFilter: OutputFilter;
  outputs: MemoirOutput[];
  visibleOutputs: MemoirOutput[];
}

function filterFromQuery(status?: string): OutputFilter {
  return status === "story_fragment" || status === "fragments" ? "story_fragment" : "all";
}

function filterOutputs(outputs: MemoirOutput[], filter: OutputFilter): MemoirOutput[] {
  if (filter === "story_fragment") {
    return outputs.filter((output) => output.output_type === "story_fragment");
  }
  return outputs;
}

function pendingReviewCount(stats?: MemoirMemoryStats): number {
  return stats ? stats.draft_memory_count + stats.open_question_count : 0;
}

Page<StoriesData, {
  onLoad(query?: Record<string, string | undefined>): Promise<void>;
  onShow(): void;
  hydrateFromCache(): MemoirDashboardSnapshot;
  applySnapshot(outputs: MemoirOutput[], memoryStats?: MemoirMemoryStats): void;
  loadStoriesPage(): Promise<void>;
  selectAll(): void;
  selectFragments(): void;
  setActiveFilter(filter: OutputFilter): void;
  continueInterview(): void;
  backWorkbench(): void;
  openHome(): void;
  openWorkbench(): void;
  openQa(): void;
  openProfile(): void;
}>({
  data: {
    loading: false,
    refreshing: false,
    hasDashboardData: false,
    hasOutputData: false,
    error: "",
    outputCount: 0,
    storyFragmentCount: 0,
    pendingReviewCount: 0,
    progressPercent: 0,
    activeFilter: "all",
    outputs: [],
    visibleOutputs: [],
  },

  async onLoad(query) {
    this.setData({ activeFilter: filterFromQuery(query?.status) });
    const snapshot = this.hydrateFromCache();
    if (shouldRefreshMemoirDashboardSnapshot(snapshot)) {
      await this.loadStoriesPage();
    }
  },

  onShow() {
    const snapshot = this.hydrateFromCache();
    if (!this.data.refreshing && shouldRefreshMemoirDashboardSnapshot(snapshot)) {
      void this.loadStoriesPage();
    }
  },

  hydrateFromCache() {
    const snapshot = readMemoirDashboardSnapshot();
    if (!snapshot.interview) {
      return snapshot;
    }
    this.applySnapshot(snapshot.outputs, snapshot.memoryStats);
    return snapshot;
  },

  applySnapshot(outputs, memoryStats) {
    this.setData({
      outputCount: memoryStats?.output_count ?? outputs.length,
      storyFragmentCount: outputs.filter((output) => output.output_type === "story_fragment").length,
      pendingReviewCount: pendingReviewCount(memoryStats),
      progressPercent: memoryStats?.coverage_average ?? 0,
      outputs,
      visibleOutputs: filterOutputs(outputs, this.data.activeFilter),
      hasDashboardData: true,
      hasOutputData: true,
    });
  },

  async loadStoriesPage() {
    if (this.data.refreshing) {
      return;
    }
    const loading = !this.data.hasDashboardData;
    this.setData({ loading, refreshing: true, error: "" });
    try {
      const { outputs, memoryStats } = await loadMemoirDashboard();
      this.applySnapshot(outputs, memoryStats);
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "写作片段加载失败" });
    } finally {
      this.setData({ loading: false, refreshing: false });
    }
  },

  selectAll() {
    this.setActiveFilter("all");
  },

  selectFragments() {
    this.setActiveFilter("story_fragment");
  },

  setActiveFilter(filter) {
    this.setData({
      activeFilter: filter,
      visibleOutputs: filterOutputs(this.data.outputs, filter),
    });
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

  openQa() {
    wx.redirectTo({ url: "/pages/qa/index" });
  },

  openProfile() {
    wx.redirectTo({ url: "/pages/profile/index" });
  },
});
