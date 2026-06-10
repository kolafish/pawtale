import type {
  MemoirCoverageItem,
  MemoirEventMemory,
  MemoirMemoryStatus,
  MemoirOpenQuestion,
  MemoirOutput,
  MemoirPersonMemory,
  MemoirPlaceMemory,
  MemoirQuestion,
  MemoirQuestionCandidate,
  MemoirSessionSummary,
  MemoirThemeMemory,
  MemoirTtsSourceType,
} from "../../domain/memoir";
import { loadMemoirDashboard } from "../../services/memoir-dashboard";
import {
  generateQuestionCandidates,
  getMemoirStoryDetail,
  processSessionMemory,
  selectQuestionCandidate,
  updateMemoryStatus,
  updateOpenQuestionStatus,
} from "../../services/memoir";
import {
  markMemoirDashboardSnapshotStale,
  readMemoirDashboardSnapshot,
  shouldRefreshMemoirDashboardSnapshot,
  writeMemoirStoryDetailSnapshot,
} from "../../stores/memoir-cache";
import { readMemoirStore, writeMemoirStore } from "../../stores/session";
import {
  cycleMemoirTtsPlaybackSpeed,
  memoirTtsSourceKey,
  pauseMemoirTtsPlayback,
  playMemoirTtsPlayback,
  readMemoirTtsPlaybackState,
  subscribeMemoirTtsPlayback,
} from "../../services/memoir-tts-player";
import { memoirTtsViewPatch } from "../../services/memoir-tts-view";

type MemoryCard =
  | { type: "person"; typeLabel: string; id: string; title: string; detail: string; status: MemoirMemoryStatus; confidenceText: string; evidence: string }
  | { type: "event"; typeLabel: string; id: string; title: string; detail: string; status: MemoirMemoryStatus; confidenceText: string; evidence: string }
  | { type: "place"; typeLabel: string; id: string; title: string; detail: string; status: MemoirMemoryStatus; confidenceText: string; evidence: string }
  | { type: "theme"; typeLabel: string; id: string; title: string; detail: string; status: MemoirMemoryStatus; confidenceText: string; evidence: string };

type ReviewOpenQuestion = MemoirOpenQuestion & {
  ttsSourceKey: string;
};

type ReviewOutput = MemoirOutput & {
  ttsSourceKey: string;
};

interface DatasetEvent {
  currentTarget: {
    dataset: {
      id?: string;
      type?: MemoryCard["type"];
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

interface ReviewData {
  answerId: string;
  sessionId: string;
  questionId: string;
  questionPrompt: string;
  questionTtsSourceKey: string;
  answerTtsSourceKey: string;
  transcript: string;
  summaryText: string;
  sessionSummaryId: string;
  sessionSummaryTtsSourceKey: string;
  loading: boolean;
  processing: boolean;
  refreshing: boolean;
  selecting: boolean;
  error: string;
  memoryCards: MemoryCard[];
  openQuestions: ReviewOpenQuestion[];
  coverage: MemoirCoverageItem[];
  candidates: MemoirQuestionCandidate[];
  outputs: ReviewOutput[];
  ttsStatus: string;
  ttsSourceKey: string;
  ttsMessage: string;
  ttsError: string;
  ttsSpeed: number;
  ttsSpeedLabel: string;
  ttsPlaying: boolean;
  ttsBusy: boolean;
}

function memoryStatusText(status: MemoirMemoryStatus): string {
  if (status === "confirmed") return "已确认";
  if (status === "rejected") return "已拒绝";
  return "待确认";
}

function confidenceText(value?: number | null): string {
  return typeof value === "number" ? `${Math.round(value * 100)}%` : "待确认";
}

function buildMemoryCards(
  people: MemoirPersonMemory[],
  events: MemoirEventMemory[],
  places: MemoirPlaceMemory[],
  themes: MemoirThemeMemory[],
): MemoryCard[] {
  return [
    ...people.map((item): MemoryCard => ({
      type: "person",
      typeLabel: "人物",
      id: item.id,
      title: item.canonical_name,
      detail: item.relationship || item.description || "人物",
      status: item.status,
      confidenceText: confidenceText(item.confidence),
      evidence: item.evidence_quote || "",
    })),
    ...events.map((item): MemoryCard => ({
      type: "event",
      typeLabel: "事件",
      id: item.id,
      title: item.title,
      detail: [item.time_text, item.place_text, item.emotion, item.process].filter(Boolean).join(" · ") || "事件",
      status: item.status,
      confidenceText: confidenceText(item.confidence),
      evidence: item.evidence_quote || "",
    })),
    ...places.map((item): MemoryCard => ({
      type: "place",
      typeLabel: "地点",
      id: item.id,
      title: item.name,
      detail: item.elder_alias || item.place_type || "地点",
      status: item.status,
      confidenceText: confidenceText(item.confidence),
      evidence: item.evidence_quote || "",
    })),
    ...themes.map((item): MemoryCard => ({
      type: "theme",
      typeLabel: "主题",
      id: item.id,
      title: item.name,
      detail: item.description || `提到 ${item.mention_count} 次`,
      status: item.status,
      confidenceText: confidenceText(item.confidence),
      evidence: item.evidence_quote || "",
    })),
  ];
}

function openQuestionView(item: MemoirOpenQuestion): ReviewOpenQuestion {
  return {
    ...item,
    ttsSourceKey: memoirTtsSourceKey({ source_type: "open_question", source_id: item.id }),
  };
}

function outputView(item: MemoirOutput): ReviewOutput {
  return {
    ...item,
    ttsSourceKey: memoirTtsSourceKey({ source_type: "output", source_id: item.id }),
  };
}

function questionTtsSourceKey(question?: MemoirQuestion): string {
  return question ? memoirTtsSourceKey({ source_type: "question", source_id: question.id }) : "";
}

function summaryTtsSourceKey(sessionSummary?: MemoirSessionSummary | null): string {
  return sessionSummary?.id
    ? memoirTtsSourceKey({ source_type: "session_summary", source_id: sessionSummary.id })
    : "";
}

Page<ReviewData, {
  unsubscribeTtsPlayback?: () => void;
  onLoad(query?: Record<string, string | undefined>): Promise<void>;
  onUnload(): void;
  registerTtsPlayback(): void;
  hydrateFromStore(answerId: string): string;
  hydrateFromDashboardSnapshot(sessionId: string): boolean;
  loadStoryDetail(answerId: string): Promise<string>;
  processAndLoad(): Promise<void>;
  loadReviewData(): Promise<void>;
  applyReviewData(input: {
    people: MemoirPersonMemory[];
    events: MemoirEventMemory[];
    places: MemoirPlaceMemory[];
    themes: MemoirThemeMemory[];
    openQuestions: MemoirOpenQuestion[];
    coverage: MemoirCoverageItem[];
    candidates: MemoirQuestionCandidate[];
    outputs: MemoirOutput[];
    summaryText: string;
    sessionSummaryId: string;
    sessionSummaryTtsSourceKey: string;
  }): void;
  toggleTtsSource(event: TtsSourceEvent): Promise<void>;
  cycleTtsSpeed(): void;
  confirmMemory(event: DatasetEvent): Promise<void>;
  rejectMemory(event: DatasetEvent): Promise<void>;
  updateMemory(event: DatasetEvent, status: MemoirMemoryStatus): Promise<void>;
  markOpenQuestionAnswered(event: DatasetEvent): Promise<void>;
  dismissOpenQuestion(event: DatasetEvent): Promise<void>;
  askOpenQuestion(event: DatasetEvent): Promise<void>;
  askCandidate(event: DatasetEvent): Promise<void>;
  continueInterview(): void;
  openStories(): void;
}>({
  data: {
    answerId: "",
    sessionId: "",
    questionId: "",
    questionPrompt: "",
    questionTtsSourceKey: "",
    answerTtsSourceKey: "",
    transcript: "",
    summaryText: "",
    sessionSummaryId: "",
    sessionSummaryTtsSourceKey: "",
    loading: false,
    processing: false,
    refreshing: false,
    selecting: false,
    error: "",
    memoryCards: [],
    openQuestions: [],
    coverage: [],
    candidates: [],
    outputs: [],
    ...memoirTtsViewPatch(readMemoirTtsPlaybackState()),
  },

  async onLoad(query) {
    this.registerTtsPlayback();
    const routeSessionId = query?.sessionId || "";
    const answerId = query?.answerId || (routeSessionId ? "" : readMemoirStore().answer?.id || "");
    this.setData({ answerId });
    const cachedSessionId = answerId ? this.hydrateFromStore(answerId) : "";
    const sessionId = routeSessionId
      || cachedSessionId
      || (answerId ? await this.loadStoryDetail(answerId) : readMemoirStore().interview?.session.id || "");
    if (!sessionId) {
      this.setData({ error: "缺少访谈信息" });
      return;
    }
    this.setData({ sessionId });
    if (answerId) {
      await this.processAndLoad();
      return;
    }
    const hydrated = this.hydrateFromDashboardSnapshot(sessionId);
    const snapshot = readMemoirDashboardSnapshot();
    if (!hydrated) {
      await this.loadReviewData();
      return;
    }
    if (shouldRefreshMemoirDashboardSnapshot(snapshot)) {
      void this.loadReviewData();
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

  hydrateFromStore(answerId) {
    const state = readMemoirStore();
    const answer = state.answer?.id === answerId ? state.answer : undefined;
    const question = state.question?.id === answer?.question_id
      ? state.question
      : state.interview?.questions.find((item) => item.id === answer?.question_id);
    this.setData({
      questionId: question?.id || this.data.questionId,
      transcript: answer?.transcript_edited || answer?.transcript_raw || "",
      questionPrompt: question?.prompt || this.data.questionPrompt,
      questionTtsSourceKey: questionTtsSourceKey(question) || this.data.questionTtsSourceKey,
      answerTtsSourceKey: answer?.id
        ? memoirTtsSourceKey({ source_type: "answer", source_id: answer.id })
        : this.data.answerTtsSourceKey,
    });
    return question?.session_id || state.interview?.session.id || "";
  },

  hydrateFromDashboardSnapshot(sessionId) {
    const snapshot = readMemoirDashboardSnapshot();
    if (!snapshot.interview || !snapshot.summary) {
      return false;
    }
    if (sessionId && snapshot.interview.session.id !== sessionId && snapshot.sessionSummary?.session_id !== sessionId) {
      return false;
    }
    this.applyReviewData({
      people: snapshot.people,
      events: snapshot.events,
      places: snapshot.places,
      themes: snapshot.themes,
      openQuestions: snapshot.openQuestions,
      coverage: snapshot.coverage,
      candidates: snapshot.candidates,
      outputs: snapshot.outputs,
      summaryText: snapshot.sessionSummary?.session_id === sessionId ? snapshot.sessionSummary.summary : "",
      sessionSummaryId: snapshot.sessionSummary?.session_id === sessionId ? snapshot.sessionSummary.id : "",
      sessionSummaryTtsSourceKey: snapshot.sessionSummary?.session_id === sessionId
        ? summaryTtsSourceKey(snapshot.sessionSummary)
        : "",
    });
    return true;
  },

  async loadStoryDetail(answerId) {
    this.setData({ loading: true, error: "" });
    try {
      const detail = await getMemoirStoryDetail(answerId);
      writeMemoirStore({
        question: detail.question,
        answer: detail.answer,
        draft: detail.draft,
      });
      writeMemoirStoryDetailSnapshot(detail);
      this.setData({
        questionId: detail.question.id,
        questionPrompt: detail.question.prompt,
        questionTtsSourceKey: questionTtsSourceKey(detail.question),
        answerTtsSourceKey: memoirTtsSourceKey({ source_type: "answer", source_id: detail.answer.id }),
        transcript: detail.answer.transcript_edited || detail.answer.transcript_raw,
      });
      return detail.question.session_id;
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "访谈材料加载失败" });
      return "";
    } finally {
      this.setData({ loading: false });
    }
  },

  async processAndLoad() {
    if (!this.data.sessionId || this.data.processing) {
      return;
    }
    this.setData({ processing: true, error: "" });
    try {
      await processSessionMemory(this.data.sessionId);
      markMemoirDashboardSnapshotStale();
      await this.loadReviewData();
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "结构化处理失败" });
    } finally {
      this.setData({ processing: false });
    }
  },

  async loadReviewData() {
    if (!this.data.sessionId) {
      return;
    }
    this.setData({ refreshing: true, error: "" });
    try {
      const {
        sessionSummary,
        people,
        events,
        places,
        themes,
        openQuestions,
        candidates,
        outputs,
        coverage,
      } = await loadMemoirDashboard();
      this.applyReviewData({
        people,
        events,
        places,
        themes,
        openQuestions,
        coverage,
        candidates,
        outputs,
        summaryText: sessionSummary?.session_id === this.data.sessionId ? sessionSummary.summary : "",
        sessionSummaryId: sessionSummary?.session_id === this.data.sessionId ? sessionSummary.id : "",
        sessionSummaryTtsSourceKey: sessionSummary?.session_id === this.data.sessionId
          ? summaryTtsSourceKey(sessionSummary)
          : "",
      });
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "记忆面板加载失败" });
    } finally {
      this.setData({ refreshing: false });
    }
  },

  applyReviewData(input) {
    this.setData({
      summaryText: input.summaryText,
      sessionSummaryId: input.sessionSummaryId,
      sessionSummaryTtsSourceKey: input.sessionSummaryTtsSourceKey,
      memoryCards: buildMemoryCards(input.people, input.events, input.places, input.themes),
      openQuestions: input.openQuestions.filter((item) => item.status === "open").map(openQuestionView),
      coverage: input.coverage,
      candidates: input.candidates,
      outputs: input.outputs.map(outputView),
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

  cycleTtsSpeed() {
    cycleMemoirTtsPlaybackSpeed();
  },

  async confirmMemory(event) {
    await this.updateMemory(event, "confirmed");
  },

  async rejectMemory(event) {
    await this.updateMemory(event, "rejected");
  },

  async updateMemory(event, status) {
    const { id, type } = event.currentTarget.dataset;
    if (!id || !type) {
      return;
    }
    try {
      await updateMemoryStatus(type, id, status);
      markMemoirDashboardSnapshotStale();
      const memoryCards = this.data.memoryCards.map((item) => (
        item.id === id && item.type === type ? { ...item, status } : item
      ));
      this.setData({ memoryCards });
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : `${memoryStatusText(status)}失败` });
    }
  },

  async markOpenQuestionAnswered(event) {
    const id = event.currentTarget.dataset.id;
    if (!id) return;
    await updateOpenQuestionStatus(id, "answered");
    markMemoirDashboardSnapshotStale();
    await this.loadReviewData();
  },

  async dismissOpenQuestion(event) {
    const id = event.currentTarget.dataset.id;
    if (!id) return;
    await updateOpenQuestionStatus(id, "dismissed");
    markMemoirDashboardSnapshotStale();
    await this.loadReviewData();
  },

  async askOpenQuestion(event) {
    const id = event.currentTarget.dataset.id;
    if (!id || this.data.selecting) return;
    this.setData({ selecting: true, error: "" });
    try {
      const openQuestion = this.data.openQuestions.find((item) => item.id === id);
      const candidates = await generateQuestionCandidates();
      const candidate = candidates.find((item) => (
        item.target_memory_type === "open_question"
        && item.target_memory_id === id
      )) || candidates.find((item) => item.question === openQuestion?.question);
      if (!candidate) {
        this.setData({ error: "没有找到可用的下一问候选" });
        return;
      }
      await selectQuestionCandidate(candidate.id);
      markMemoirDashboardSnapshotStale();
      wx.redirectTo({ url: "/pages/question/index" });
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "下一问选择失败" });
    } finally {
      this.setData({ selecting: false });
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
      wx.redirectTo({ url: "/pages/question/index" });
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "下一问选择失败" });
    } finally {
      this.setData({ selecting: false });
    }
  },

  continueInterview() {
    wx.redirectTo({ url: "/pages/question/index" });
  },

  openStories() {
    wx.redirectTo({ url: "/pages/stories/index" });
  },
});
