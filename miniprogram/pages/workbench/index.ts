import type {
  MemoirCoverageItem,
  MemoirEventMemory,
  MemoirInterviewState,
  MemoirMemoryStats,
  MemoirMemoryStatus,
  MemoirOpenQuestion,
  MemoirOutput,
  MemoirPersonMemory,
  MemoirPlaceMemory,
  MemoirQuestionCandidate,
  MemoirSummary,
  MemoirThemeMemory,
} from "../../domain/memoir";
import { loadMemoirDashboard } from "../../services/memoir-dashboard";
import {
  readMemoirDashboardSnapshot,
} from "../../stores/memoir-cache";

interface WorkbenchData {
  loading: boolean;
  refreshing: boolean;
  hasDashboardData: boolean;
  hasOutputData: boolean;
  error: string;
  weeklyStoryCount: number;
  pendingReviewCount: number;
  confirmedStoryCount: number;
  progressPercent: number;
  pendingCount: number;
  summaryText: string;
  coverage: MemoirCoverageItem[];
  memoryCards: Array<{ title: string; detail: string; status: MemoirMemoryStatus }>;
  openQuestions: MemoirOpenQuestion[];
  candidates: MemoirQuestionCandidate[];
  outputs: MemoirOutput[];
}

function buildMemoryCards(
  people: MemoirPersonMemory[],
  events: MemoirEventMemory[],
  places: MemoirPlaceMemory[],
  themes: MemoirThemeMemory[],
): Array<{ title: string; detail: string; status: MemoirMemoryStatus }> {
  return [
    ...people.map((item) => ({
      title: item.canonical_name,
      detail: item.relationship || item.description || "人物",
      status: item.status,
    })),
    ...events.map((item) => ({
      title: item.title,
      detail: [item.time_text, item.place_text, item.emotion].filter(Boolean).join(" · ") || "事件",
      status: item.status,
    })),
    ...places.map((item) => ({
      title: item.name,
      detail: item.elder_alias || item.place_type || "地点",
      status: item.status,
    })),
    ...themes.map((item) => ({
      title: item.name,
      detail: item.description || `提到 ${item.mention_count} 次`,
      status: item.status,
    })),
  ];
}

Page<WorkbenchData, {
  onLoad(): Promise<void>;
  onShow(): void;
  hydrateFromCache(): void;
  applySnapshot(
    interview: MemoirInterviewState,
    summary: MemoirSummary,
    memory?: {
      summaryText?: string;
      coverage: MemoirCoverageItem[];
      people: MemoirPersonMemory[];
      events: MemoirEventMemory[];
      places: MemoirPlaceMemory[];
      themes: MemoirThemeMemory[];
      openQuestions: MemoirOpenQuestion[];
      candidates: MemoirQuestionCandidate[];
      outputs: MemoirOutput[];
      memoryStats?: MemoirMemoryStats;
    },
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
    hasOutputData: false,
    error: "",
    weeklyStoryCount: 0,
    pendingReviewCount: 0,
    confirmedStoryCount: 0,
    progressPercent: 0,
    pendingCount: 0,
    summaryText: "",
    coverage: [],
    memoryCards: [],
    openQuestions: [],
    candidates: [],
    outputs: [],
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
    this.applySnapshot(snapshot.interview, snapshot.summary, {
      summaryText: snapshot.sessionSummary?.summary || "",
      coverage: snapshot.coverage,
      people: snapshot.people,
      events: snapshot.events,
      places: snapshot.places,
      themes: snapshot.themes,
      openQuestions: snapshot.openQuestions,
      candidates: snapshot.candidates,
      outputs: snapshot.outputs,
      memoryStats: snapshot.memoryStats,
    });
  },

  applySnapshot(interview, summary, memory) {
    const memoryStats = memory?.memoryStats;
    const pendingReviewCount = memoryStats
      ? memoryStats.draft_memory_count + memoryStats.open_question_count
      : summary.pending_review_count;
    this.setData({
      weeklyStoryCount: memoryStats?.total_memory_count ?? summary.weekly_story_count,
      pendingReviewCount,
      confirmedStoryCount: memoryStats?.output_count ?? summary.confirmed_story_count,
      progressPercent: memoryStats?.coverage_average ?? summary.progress_percent,
      pendingCount: interview.pending_question_count,
      summaryText: memory?.summaryText || this.data.summaryText,
      coverage: memory?.coverage || this.data.coverage,
      memoryCards: memory ? buildMemoryCards(memory.people, memory.events, memory.places, memory.themes) : this.data.memoryCards,
      openQuestions: (memory?.openQuestions || this.data.openQuestions).filter((item) => item.status === "open"),
      candidates: memory?.candidates || this.data.candidates,
      outputs: memory?.outputs || this.data.outputs,
      hasDashboardData: true,
      hasOutputData: true,
    });
  },

  async loadWorkbench() {
    if (this.data.refreshing) {
      return;
    }
    const loading = !this.data.hasDashboardData;
    this.setData({ loading, refreshing: true, error: "" });
    try {
      const {
        interview,
        summary,
        sessionSummary,
        people,
        events,
        places,
        themes,
        openQuestions,
        coverage,
        candidates,
        outputs,
        memoryStats,
      } = await loadMemoirDashboard({ includeMemory: true });
      this.applySnapshot(interview, summary, {
        summaryText: sessionSummary?.summary || "",
        coverage,
        people,
        events,
        places,
        themes,
        openQuestions,
        candidates,
        outputs,
        memoryStats,
      });
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
