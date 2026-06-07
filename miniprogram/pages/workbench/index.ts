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
import { selectQuestionCandidate } from "../../services/memoir";
import {
  readMemoirDashboardSnapshot,
} from "../../stores/memoir-cache";

const REVIEW_NAVIGATION_WAIT_MS = 100;
const REVIEW_NAVIGATION_MAX_ATTEMPTS = 600;

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

type WorkbenchMemoryCard = {
  title: string;
  detail: string;
  typeLabel: string;
  status: MemoirMemoryStatus;
  confidenceText: string;
  evidence: string;
};

interface DatasetEvent {
  currentTarget: {
    dataset: {
      id?: string;
    };
  };
}

interface WorkbenchData {
  loading: boolean;
  refreshing: boolean;
  selecting: boolean;
  hasDashboardData: boolean;
  hasOutputData: boolean;
  error: string;
  weeklyStoryCount: number;
  pendingReviewCount: number;
  confirmedStoryCount: number;
  progressPercent: number;
  pendingCount: number;
  summaryText: string;
  reviewSessionId: string;
  coverage: MemoirCoverageItem[];
  memoryCards: WorkbenchMemoryCard[];
  openQuestions: MemoirOpenQuestion[];
  candidates: MemoirQuestionCandidate[];
  outputs: MemoirOutput[];
}

function confidenceText(value?: number | null): string {
  return typeof value === "number" ? `${Math.round(value * 100)}%` : "待确认";
}

function buildMemoryCards(
  people: MemoirPersonMemory[],
  events: MemoirEventMemory[],
  places: MemoirPlaceMemory[],
  themes: MemoirThemeMemory[],
): WorkbenchMemoryCard[] {
  return [
    ...people.map((item) => ({
      title: item.canonical_name,
      detail: item.relationship || item.description || "人物",
      typeLabel: "人物",
      status: item.status,
      confidenceText: confidenceText(item.confidence),
      evidence: item.evidence_quote || "",
    })),
    ...events.map((item) => ({
      title: item.title,
      detail: [item.time_text, item.place_text, item.emotion].filter(Boolean).join(" · ") || "事件",
      typeLabel: "事件",
      status: item.status,
      confidenceText: confidenceText(item.confidence),
      evidence: item.evidence_quote || "",
    })),
    ...places.map((item) => ({
      title: item.name,
      detail: item.elder_alias || item.place_type || "地点",
      typeLabel: "地点",
      status: item.status,
      confidenceText: confidenceText(item.confidence),
      evidence: item.evidence_quote || "",
    })),
    ...themes.map((item) => ({
      title: item.name,
      detail: item.description || `提到 ${item.mention_count} 次`,
      typeLabel: "主题",
      status: item.status,
      confidenceText: confidenceText(item.confidence),
      evidence: item.evidence_quote || "",
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
  askCandidate(event: DatasetEvent): Promise<void>;
  continueInterview(): void;
  openPendingReview(): Promise<void>;
  openStories(): void;
  backHome(): void;
  openHome(): void;
  openWorkbench(): void;
  openQa(): void;
  openProfile(): void;
}>({
  data: {
    loading: false,
    refreshing: false,
    selecting: false,
    hasDashboardData: false,
    hasOutputData: false,
    error: "",
    weeklyStoryCount: 0,
    pendingReviewCount: 0,
    confirmedStoryCount: 0,
    progressPercent: 0,
    pendingCount: 0,
    summaryText: "",
    reviewSessionId: "",
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
      reviewSessionId: interview.session.id,
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

  async askCandidate(event) {
    const id = event.currentTarget.dataset.id;
    if (!id || this.data.selecting) {
      return;
    }
    this.setData({ selecting: true, error: "" });
    try {
      await selectQuestionCandidate(id);
      wx.navigateTo({ url: "/pages/question/index" });
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "下一问选择失败" });
    } finally {
      this.setData({ selecting: false });
    }
  },

  continueInterview() {
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
      await this.loadWorkbench();
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

  backHome() {
    wx.redirectTo({ url: "/pages/home/index" });
  },

  openHome() {
    wx.redirectTo({ url: "/pages/home/index" });
  },

  openWorkbench() {
  },

  openQa() {
    wx.redirectTo({ url: "/pages/qa/index" });
  },

  openProfile() {
    wx.redirectTo({ url: "/pages/profile/index" });
  },
});
