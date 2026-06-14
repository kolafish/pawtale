import type { MemoirMemoryStats, MemoirOutput, MemoirTtsSourceType } from "../../domain/memoir";
import { loadMemoirDashboard } from "../../services/memoir-dashboard";
import {
  type MemoirDashboardSnapshot,
  readMemoirDashboardSnapshot,
  shouldRefreshMemoirDashboardSnapshot,
} from "../../stores/memoir-cache";
import {
  memoirTtsSourceKey,
  pauseMemoirTtsPlayback,
  playMemoirTtsPlayback,
  readMemoirTtsPlaybackState,
  subscribeMemoirTtsPlayback,
} from "../../services/memoir-tts-player";
import { memoirTtsViewPatch } from "../../services/memoir-tts-view";

type OutputFilter = "all" | "story_fragment";
type StoryOutput = MemoirOutput & {
  ttsSourceKey: string;
};

interface TtsSourceEvent {
  currentTarget: {
    dataset: {
      sourceType?: MemoirTtsSourceType;
      sourceId?: string;
      label?: string;
    };
  };
}

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
  outputs: StoryOutput[];
  visibleOutputs: StoryOutput[];
  ttsStatus: string;
  ttsSourceKey: string;
  ttsMessage: string;
  ttsError: string;
  ttsPlaying: boolean;
  ttsBusy: boolean;
}

function filterFromQuery(status?: string): OutputFilter {
  return status === "story_fragment" || status === "fragments" ? "story_fragment" : "all";
}

function outputView(output: MemoirOutput): StoryOutput {
  return {
    ...output,
    ttsSourceKey: memoirTtsSourceKey({ source_type: "output", source_id: output.id }),
  };
}

function filterOutputs(outputs: StoryOutput[], filter: OutputFilter): StoryOutput[] {
  if (filter === "story_fragment") {
    return outputs.filter((output) => output.output_type === "story_fragment");
  }
  return outputs;
}

function pendingReviewCount(stats?: MemoirMemoryStats): number {
  return stats ? stats.draft_memory_count + stats.open_question_count : 0;
}

Page<StoriesData, {
  unsubscribeTtsPlayback?: () => void;
  onLoad(query?: Record<string, string | undefined>): Promise<void>;
  onShow(): void;
  onUnload(): void;
  registerTtsPlayback(): void;
  hydrateFromCache(): MemoirDashboardSnapshot;
  applySnapshot(outputs: MemoirOutput[], memoryStats?: MemoirMemoryStats): void;
  loadStoriesPage(): Promise<void>;
  selectAll(): void;
  selectFragments(): void;
  setActiveFilter(filter: OutputFilter): void;
  toggleTtsSource(event: TtsSourceEvent): Promise<void>;
  continueInterview(): void;
  backWorkbench(): void;
  openHome(): void;
  openWorkbench(): void;
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
    ...memoirTtsViewPatch(readMemoirTtsPlaybackState()),
  },

  async onLoad(query) {
    this.registerTtsPlayback();
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
    if (!snapshot.interview) {
      return snapshot;
    }
    this.applySnapshot(snapshot.outputs, snapshot.memoryStats);
    return snapshot;
  },

  applySnapshot(outputs, memoryStats) {
    const outputViews = outputs.map(outputView);
    this.setData({
      outputCount: memoryStats?.output_count ?? outputs.length,
      storyFragmentCount: outputs.filter((output) => output.output_type === "story_fragment").length,
      pendingReviewCount: pendingReviewCount(memoryStats),
      progressPercent: memoryStats?.coverage_average ?? 0,
      outputs: outputViews,
      visibleOutputs: filterOutputs(outputViews, this.data.activeFilter),
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

  continueInterview() {
    wx.navigateTo({ url: "/pages/voice-start/index" });
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

  openProfile() {
    wx.redirectTo({ url: "/pages/profile/index" });
  },
});
