import type {
  MemoirCoverageItem,
  MemoirEventMemory,
  MemoirMemoryStatus,
  MemoirOpenQuestion,
  MemoirOutput,
  MemoirPersonMemory,
  MemoirPlaceMemory,
  MemoirQuestionCandidate,
  MemoirThemeMemory,
} from "../../domain/memoir";
import {
  generateQuestionCandidates,
  getMemoirStoryDetail,
  getCoverageMap,
  getSessionSummary,
  listEventMemories,
  listMemoirOutputs,
  listOpenQuestions,
  listPeopleMemories,
  listPlaceMemories,
  listThemeMemories,
  processSessionMemory,
  selectQuestionCandidate,
  updateMemoryStatus,
  updateOpenQuestionStatus,
} from "../../services/memoir";
import { writeMemoirDashboardSnapshot } from "../../stores/memoir-cache";
import { readMemoirStore, writeMemoirStore } from "../../stores/session";

type MemoryCard =
  | { type: "person"; id: string; title: string; detail: string; status: MemoirMemoryStatus }
  | { type: "event"; id: string; title: string; detail: string; status: MemoirMemoryStatus }
  | { type: "place"; id: string; title: string; detail: string; status: MemoirMemoryStatus }
  | { type: "theme"; id: string; title: string; detail: string; status: MemoirMemoryStatus };

interface DatasetEvent {
  currentTarget: {
    dataset: {
      id?: string;
      type?: MemoryCard["type"];
    };
  };
}

interface ReviewData {
  answerId: string;
  sessionId: string;
  questionPrompt: string;
  transcript: string;
  summaryText: string;
  loading: boolean;
  processing: boolean;
  refreshing: boolean;
  selecting: boolean;
  error: string;
  memoryCards: MemoryCard[];
  openQuestions: MemoirOpenQuestion[];
  coverage: MemoirCoverageItem[];
  candidates: MemoirQuestionCandidate[];
  outputs: MemoirOutput[];
}

function memoryStatusText(status: MemoirMemoryStatus): string {
  if (status === "confirmed") return "已确认";
  if (status === "rejected") return "已拒绝";
  return "待确认";
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
      id: item.id,
      title: item.canonical_name,
      detail: item.relationship || item.description || "人物",
      status: item.status,
    })),
    ...events.map((item): MemoryCard => ({
      type: "event",
      id: item.id,
      title: item.title,
      detail: [item.time_text, item.place_text, item.emotion].filter(Boolean).join(" · ") || "事件",
      status: item.status,
    })),
    ...places.map((item): MemoryCard => ({
      type: "place",
      id: item.id,
      title: item.name,
      detail: item.elder_alias || item.place_type || "地点",
      status: item.status,
    })),
    ...themes.map((item): MemoryCard => ({
      type: "theme",
      id: item.id,
      title: item.name,
      detail: item.description || `提到 ${item.mention_count} 次`,
      status: item.status,
    })),
  ];
}

Page<ReviewData, {
  onLoad(query?: Record<string, string | undefined>): Promise<void>;
  hydrateFromStore(answerId: string): string;
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
  }): void;
  confirmMemory(event: DatasetEvent): Promise<void>;
  rejectMemory(event: DatasetEvent): Promise<void>;
  updateMemory(event: DatasetEvent, status: MemoirMemoryStatus): Promise<void>;
  markOpenQuestionAnswered(event: DatasetEvent): Promise<void>;
  dismissOpenQuestion(event: DatasetEvent): Promise<void>;
  askCandidate(event: DatasetEvent): Promise<void>;
  continueInterview(): void;
  openStories(): void;
}>({
  data: {
    answerId: "",
    sessionId: "",
    questionPrompt: "",
    transcript: "",
    summaryText: "",
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
  },

  async onLoad(query) {
    const answerId = query?.answerId || readMemoirStore().answer?.id || "";
    this.setData({ answerId });
    const cachedSessionId = answerId ? this.hydrateFromStore(answerId) : "";
    const sessionId = cachedSessionId || (answerId ? await this.loadStoryDetail(answerId) : "");
    if (!sessionId) {
      this.setData({ error: "缺少访谈信息" });
      return;
    }
    this.setData({ sessionId });
    await this.processAndLoad();
  },

  hydrateFromStore(answerId) {
    const state = readMemoirStore();
    const answer = state.answer?.id === answerId ? state.answer : undefined;
    const question = state.question?.id === answer?.question_id
      ? state.question
      : state.interview?.questions.find((item) => item.id === answer?.question_id);
    this.setData({
      transcript: answer?.transcript_edited || answer?.transcript_raw || "",
      questionPrompt: question?.prompt || this.data.questionPrompt,
    });
    return question?.session_id || state.interview?.session.id || "";
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
      this.setData({
        questionPrompt: detail.question.prompt,
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
      const [
        sessionSummary,
        people,
        events,
        places,
        themes,
        openQuestions,
        candidates,
        outputs,
      ] = await Promise.all([
        getSessionSummary(this.data.sessionId),
        listPeopleMemories(),
        listEventMemories(),
        listPlaceMemories(),
        listThemeMemories(),
        listOpenQuestions(),
        generateQuestionCandidates(),
        listMemoirOutputs(),
      ]);
      const coverage = await getCoverageMap();
      this.applyReviewData({
        people,
        events,
        places,
        themes,
        openQuestions,
        coverage,
        candidates,
        outputs,
        summaryText: sessionSummary?.summary || "",
      });
      writeMemoirDashboardSnapshot({
        sessionSummary,
        people,
        events,
        places,
        themes,
        openQuestions,
        coverage,
        candidates,
        outputs,
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
      memoryCards: buildMemoryCards(input.people, input.events, input.places, input.themes),
      openQuestions: input.openQuestions.filter((item) => item.status === "open"),
      coverage: input.coverage,
      candidates: input.candidates,
      outputs: input.outputs,
    });
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
    await this.loadReviewData();
  },

  async dismissOpenQuestion(event) {
    const id = event.currentTarget.dataset.id;
    if (!id) return;
    await updateOpenQuestionStatus(id, "dismissed");
    await this.loadReviewData();
  },

  async askCandidate(event) {
    const id = event.currentTarget.dataset.id;
    if (!id || this.data.selecting) {
      return;
    }
    this.setData({ selecting: true, error: "" });
    try {
      await selectQuestionCandidate(id);
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
