import { acceptDraft, generateDraft, getMemoirStoryDetail } from "../../services/memoir";
import { updateMemoirCachedStoryDraft } from "../../stores/memoir-cache";
import { readMemoirStore, writeMemoirStore } from "../../stores/session";

interface FeedbackData {
  answerId: string;
  draftId: string;
  questionPrompt: string;
  transcript: string;
  draftText: string;
  isAccepted: boolean;
  returnTo: string;
  loading: boolean;
  generating: boolean;
  accepting: boolean;
  error: string;
}

Page<FeedbackData, {
  onLoad(query?: Record<string, string | undefined>): Promise<void>;
  onShow(): void;
  hydrateFromStore(answerId: string): boolean;
  loadStoryDetail(answerId: string): Promise<boolean>;
  generate(answerId: string): Promise<void>;
  retryGenerate(): Promise<void>;
  openEditor(): void;
  acceptCurrentDraft(): Promise<void>;
  backToStories(): void;
}>({
  data: {
    answerId: "",
    draftId: "",
    questionPrompt: "",
    transcript: "",
    draftText: "",
    isAccepted: false,
    returnTo: "",
    loading: false,
    generating: false,
    accepting: false,
    error: "",
  },

  async onLoad(query) {
    const answerId = query?.answerId || readMemoirStore().answer?.id || "";
    const returnTo = query?.returnTo || "";
    this.setData({ answerId, returnTo });
    if (!answerId) {
      this.setData({ error: "缺少回答信息" });
      return;
    }
    let hasDraft = this.hydrateFromStore(answerId);
    hasDraft = await this.loadStoryDetail(answerId) || hasDraft;
    if (!hasDraft) {
      await this.generate(answerId);
    }
  },

  onShow() {
    if (this.data.answerId) {
      this.hydrateFromStore(this.data.answerId);
    }
  },

  hydrateFromStore(answerId) {
    const state = readMemoirStore();
    const answer = state.answer?.id === answerId ? state.answer : undefined;
    const draft = state.draft;
    const question = state.question?.id === answer?.question_id
      ? state.question
      : state.interview?.questions.find((item) => item.id === answer?.question_id);
    this.setData({
      transcript: answer?.transcript_edited || answer?.transcript_raw || "",
      questionPrompt: question?.prompt || this.data.questionPrompt,
    });
    if (draft && draft.answer_id === answerId) {
      this.setData({
        draftId: draft.id,
        draftText: draft.content_edited || draft.content_raw,
        isAccepted: draft.status === "accepted",
      });
      return true;
    }
    return false;
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
        draftId: detail.draft?.id || "",
        draftText: detail.draft?.content_edited || detail.draft?.content_raw || "",
        isAccepted: detail.draft?.status === "accepted",
      });
      return Boolean(detail.draft);
    } catch (error) {
      if (!this.data.transcript) {
        this.setData({ error: error instanceof Error ? error.message : "故事加载失败" });
      }
      return false;
    } finally {
      this.setData({ loading: false });
    }
  },

  async generate(answerId) {
    if (this.data.isAccepted) {
      return;
    }
    this.setData({ generating: true, error: "" });
    try {
      const draft = await generateDraft(answerId);
      writeMemoirStore({ draft });
      updateMemoirCachedStoryDraft(draft);
      this.setData({
        draftId: draft.id,
        draftText: draft.content_edited || draft.content_raw,
        isAccepted: false,
      });
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "整理失败" });
    } finally {
      this.setData({ generating: false });
    }
  },

  async retryGenerate() {
    if (!this.data.answerId || this.data.isAccepted) {
      return;
    }
    await this.generate(this.data.answerId);
  },

  openEditor() {
    if (!this.data.answerId || !this.data.draftId || this.data.isAccepted) {
      return;
    }
    const returnTo = this.data.returnTo ? `&returnTo=${this.data.returnTo}` : "";
    wx.navigateTo({
      url: `/pages/editor/index?answerId=${this.data.answerId}&draftId=${this.data.draftId}${returnTo}`,
    });
  },

  async acceptCurrentDraft() {
    if (!this.data.draftId || this.data.isAccepted) {
      return;
    }
    this.setData({ accepting: true, error: "" });
    try {
      const draft = await acceptDraft(this.data.draftId);
      writeMemoirStore({ draft });
      updateMemoirCachedStoryDraft(draft);
      if (this.data.returnTo === "stories") {
        wx.redirectTo({ url: "/pages/stories/index?status=accepted" });
        return;
      }
      wx.redirectTo({ url: "/pages/question/index" });
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "确认失败" });
    } finally {
      this.setData({ accepting: false });
    }
  },

  backToStories() {
    wx.redirectTo({ url: "/pages/stories/index" });
  },
});
