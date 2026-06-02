import { acceptDraft, generateDraft } from "../../services/memoir";
import { readMemoirStore, writeMemoirStore } from "../../stores/session";

interface FeedbackData {
  answerId: string;
  draftId: string;
  transcript: string;
  draftText: string;
  generating: boolean;
  accepting: boolean;
  error: string;
}

Page<FeedbackData, {
  onLoad(query?: Record<string, string | undefined>): Promise<void>;
  onShow(): void;
  hydrateFromStore(answerId: string): boolean;
  generate(answerId: string): Promise<void>;
  retryGenerate(): Promise<void>;
  openEditor(): void;
  acceptCurrentDraft(): Promise<void>;
}>({
  data: {
    answerId: "",
    draftId: "",
    transcript: "",
    draftText: "",
    generating: false,
    accepting: false,
    error: "",
  },

  async onLoad(query) {
    const answerId = query?.answerId || readMemoirStore().answer?.id || "";
    this.setData({ answerId });
    if (!answerId) {
      this.setData({ error: "缺少回答信息" });
      return;
    }
    const hasDraft = this.hydrateFromStore(answerId);
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
    const answer = state.answer;
    const draft = state.draft;
    this.setData({
      transcript: answer?.transcript_edited || answer?.transcript_raw || "",
    });
    if (draft && draft.answer_id === answerId) {
      this.setData({
        draftId: draft.id,
        draftText: draft.content_edited || draft.content_raw,
      });
      return true;
    }
    return false;
  },

  async generate(answerId) {
    this.setData({ generating: true, error: "" });
    try {
      const draft = await generateDraft(answerId);
      writeMemoirStore({ draft });
      this.setData({
        draftId: draft.id,
        draftText: draft.content_edited || draft.content_raw,
      });
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "整理失败" });
    } finally {
      this.setData({ generating: false });
    }
  },

  async retryGenerate() {
    if (!this.data.answerId) {
      return;
    }
    await this.generate(this.data.answerId);
  },

  openEditor() {
    if (!this.data.answerId || !this.data.draftId) {
      return;
    }
    wx.navigateTo({
      url: `/pages/editor/index?answerId=${this.data.answerId}&draftId=${this.data.draftId}`,
    });
  },

  async acceptCurrentDraft() {
    if (!this.data.draftId) {
      return;
    }
    this.setData({ accepting: true, error: "" });
    try {
      const draft = await acceptDraft(this.data.draftId);
      writeMemoirStore({ draft });
      wx.redirectTo({ url: "/pages/question/index" });
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "确认失败" });
    } finally {
      this.setData({ accepting: false });
    }
  },
});
