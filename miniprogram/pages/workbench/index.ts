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
  MemoirTtsSourceType,
} from "../../domain/memoir";
import { loadMemoirDashboard } from "../../services/memoir-dashboard";
import { selectQuestionCandidate } from "../../services/memoir";
import {
  markMemoirDashboardSnapshotStale,
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

type WorkbenchMemoryCard = {
  title: string;
  detail: string;
  typeLabel: string;
  status: MemoirMemoryStatus;
  confidenceText: string;
  evidence: string;
};

type WorkbenchOpenQuestion = MemoirOpenQuestion & {
  ttsSourceKey: string;
};

type WorkbenchOutput = MemoirOutput & {
  ttsSourceKey: string;
};

interface DatasetEvent {
  currentTarget: {
    dataset: {
      id?: string;
    };
  };
}

interface TtsSourceEvent {
  currentTarget: {
    dataset: {
      sourceType?: MemoirTtsSourceType;
      sourceId?: string;
      label?: string;
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
  projectTitle: string;
  weeklyStoryCount: number;
  pendingReviewCount: number;
  confirmedStoryCount: number;
  progressPercent: number;
  pendingCount: number;
  sessionSummaryId: string;
  sessionSummaryTtsSourceKey: string;
  summaryText: string;
  reviewSessionId: string;
  coverage: MemoirCoverageItem[];
  memoryCards: WorkbenchMemoryCard[];
  openQuestions: WorkbenchOpenQuestion[];
  candidates: MemoirQuestionCandidate[];
  outputs: WorkbenchOutput[];
  ttsStatus: string;
  ttsSourceKey: string;
  ttsMessage: string;
  ttsError: string;
  ttsPlaying: boolean;
  ttsBusy: boolean;
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

function openQuestionView(item: MemoirOpenQuestion): WorkbenchOpenQuestion {
  return {
    ...item,
    ttsSourceKey: memoirTtsSourceKey({ source_type: "open_question", source_id: item.id }),
  };
}

function outputView(item: MemoirOutput): WorkbenchOutput {
  return {
    ...item,
    ttsSourceKey: memoirTtsSourceKey({ source_type: "output", source_id: item.id }),
  };
}

Page<WorkbenchData, {
  unsubscribeTtsPlayback?: () => void;
  onLoad(): Promise<void>;
  onShow(): void;
  onUnload(): void;
  registerTtsPlayback(): void;
  hydrateFromCache(): MemoirDashboardSnapshot;
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
      sessionSummaryId?: string;
    },
  ): void;
  loadWorkbench(): Promise<void>;
  askCandidate(event: DatasetEvent): Promise<void>;
  toggleTtsSource(event: TtsSourceEvent): Promise<void>;
  continueInterview(): void;
  openPendingReview(): Promise<void>;
  openProjects(): void;
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
    projectTitle: "",
    weeklyStoryCount: 0,
    pendingReviewCount: 0,
    confirmedStoryCount: 0,
    progressPercent: 0,
    pendingCount: 0,
    sessionSummaryId: "",
    sessionSummaryTtsSourceKey: "",
    summaryText: "",
    reviewSessionId: "",
    coverage: [],
    memoryCards: [],
    openQuestions: [],
    candidates: [],
    outputs: [],
    ...memoirTtsViewPatch(readMemoirTtsPlaybackState()),
  },

  async onLoad() {
    this.registerTtsPlayback();
    const snapshot = this.hydrateFromCache();
    if (shouldRefreshMemoirDashboardSnapshot(snapshot)) {
      await this.loadWorkbench();
    }
  },

  onShow() {
    const snapshot = this.hydrateFromCache();
    if (!this.data.refreshing && shouldRefreshMemoirDashboardSnapshot(snapshot)) {
      void this.loadWorkbench();
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
    if (!snapshot.interview || !snapshot.summary) {
      return snapshot;
    }
    this.setData({
      projectTitle: snapshot.project?.title || this.data.projectTitle,
    });
    this.applySnapshot(snapshot.interview, snapshot.summary, {
      summaryText: snapshot.sessionSummary?.summary || "",
      sessionSummaryId: snapshot.sessionSummary?.id || "",
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
    return snapshot;
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
      sessionSummaryId: memory?.sessionSummaryId || this.data.sessionSummaryId,
      sessionSummaryTtsSourceKey: memory?.sessionSummaryId
        ? memoirTtsSourceKey({ source_type: "session_summary", source_id: memory.sessionSummaryId })
        : this.data.sessionSummaryTtsSourceKey,
      reviewSessionId: interview.session.id,
      summaryText: memory?.summaryText || this.data.summaryText,
      coverage: memory?.coverage || this.data.coverage,
      memoryCards: memory ? buildMemoryCards(memory.people, memory.events, memory.places, memory.themes) : this.data.memoryCards,
      openQuestions: (memory?.openQuestions || this.data.openQuestions)
        .filter((item) => item.status === "open")
        .map(openQuestionView),
      candidates: memory?.candidates || this.data.candidates,
      outputs: (memory?.outputs || this.data.outputs).map(outputView),
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
        project,
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
      } = await loadMemoirDashboard();
      this.setData({
        projectTitle: project?.title || this.data.projectTitle,
      });
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
        sessionSummaryId: sessionSummary?.id || "",
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
      markMemoirDashboardSnapshotStale();
      wx.navigateTo({ url: "/pages/question/index" });
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "下一问选择失败" });
    } finally {
      this.setData({ selecting: false });
    }
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

  async openPendingReview() {
    const snapshot = readMemoirDashboardSnapshot();
    const sessionId = this.data.reviewSessionId || snapshot.interview?.session.id || "";
    if (sessionId) {
      wx.navigateTo({ url: `/pages/review/index?sessionId=${encodeURIComponent(sessionId)}` });
      return;
    }
    if (!this.data.refreshing) {
      await this.loadWorkbench();
    }
    const fallbackSessionId = this.data.reviewSessionId || readMemoirDashboardSnapshot().interview?.session.id || "";
    if (!fallbackSessionId) {
      this.setData({ error: "缺少访谈信息" });
      return;
    }
    wx.navigateTo({ url: `/pages/review/index?sessionId=${encodeURIComponent(fallbackSessionId)}` });
  },

  openProjects() {
    wx.navigateTo({ url: "/pages/projects/index" });
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
