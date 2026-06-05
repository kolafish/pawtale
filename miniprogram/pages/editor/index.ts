import { acceptDraft, getMemoirStoryDetail, updateDraft, updateTextAnswer } from "../../services/memoir";
import { updateMemoirCachedStoryAnswer, updateMemoirCachedStoryDraft } from "../../stores/memoir-cache";
import { readMemoirStore, writeMemoirStore } from "../../stores/session";

interface InputEvent {
  detail: {
    value: string;
  };
}

interface EditorData {
  answerId: string;
  draftId: string;
  questionPrompt: string;
  transcriptText: string;
  draftText: string;
  returnTo: string;
  transcriptFocus: boolean;
  draftFocus: boolean;
  loading: boolean;
  contentReady: boolean;
  saving: boolean;
  error: string;
}

Page<EditorData, {
  onLoad(query?: Record<string, string | undefined>): Promise<void>;
  hydrateFromMatchingStore(answerId: string, draftId: string): boolean;
  loadEditorContent(answerId: string, draftId: string): Promise<void>;
  goBack(): void;
  onTranscriptInput(event: InputEvent): void;
  onDraftInput(event: InputEvent): void;
  focusTranscript(): void;
  focusDraft(): void;
  saveEdits(showToast: boolean): Promise<boolean>;
  saveEditsOnly(): Promise<void>;
  saveAndContinue(): Promise<void>;
}>({
  data: {
    answerId: "",
    draftId: "",
    questionPrompt: "",
    transcriptText: "",
    draftText: "",
    returnTo: "",
    transcriptFocus: false,
    draftFocus: false,
    loading: false,
    contentReady: false,
    saving: false,
    error: "",
  },

  async onLoad(query) {
    const state = readMemoirStore();
    const answerId = query?.answerId || state.answer?.id || "";
    const draftId = query?.draftId || state.draft?.id || "";
    const returnTo = query?.returnTo || "";
    this.setData({
      answerId,
      draftId,
      returnTo,
      error: !answerId || !draftId ? "缺少编辑内容" : "",
    });
    if (!answerId || !draftId) {
      return;
    }
    this.hydrateFromMatchingStore(answerId, draftId);
    await this.loadEditorContent(answerId, draftId);
  },

  hydrateFromMatchingStore(answerId, draftId) {
    const state = readMemoirStore();
    const answer = state.answer?.id === answerId ? state.answer : undefined;
    const draft = state.draft?.id === draftId && state.draft.answer_id === answerId
      ? state.draft
      : undefined;
    if (!answer || !draft) {
      return false;
    }
    const question = state.question?.id === answer.question_id
      ? state.question
      : state.interview?.questions.find((item) => item.id === answer.question_id);
    this.setData({
      questionPrompt: question?.prompt || "",
      transcriptText: answer.transcript_edited || answer.transcript_raw || "",
      draftText: draft.content_edited || draft.content_raw || "",
    });
    return true;
  },

  async loadEditorContent(answerId, draftId) {
    this.setData({ loading: true, contentReady: false, error: "" });
    try {
      const detail = await getMemoirStoryDetail(answerId);
      if (!detail.draft || detail.draft.id !== draftId || detail.draft.answer_id !== answerId) {
        this.setData({ error: "编辑内容已更新，请返回重试" });
        return;
      }
      writeMemoirStore({
        question: detail.question,
        answer: detail.answer,
        draft: detail.draft,
      });
      this.setData({
        questionPrompt: detail.question.prompt,
        transcriptText: detail.answer.transcript_edited || detail.answer.transcript_raw,
        draftText: detail.draft.content_edited || detail.draft.content_raw,
        contentReady: true,
      });
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "编辑内容加载失败" });
    } finally {
      this.setData({ loading: false });
    }
  },

  goBack() {
    if (this.data.answerId) {
      const returnTo = this.data.returnTo ? `&returnTo=${this.data.returnTo}` : "";
      wx.redirectTo({ url: `/pages/review/index?answerId=${this.data.answerId}${returnTo}` });
      return;
    }
    wx.redirectTo({ url: "/pages/home/index" });
  },

  onTranscriptInput(event) {
    this.setData({ transcriptText: event.detail.value, transcriptFocus: false });
  },

  onDraftInput(event) {
    this.setData({ draftText: event.detail.value, draftFocus: false });
  },

  focusTranscript() {
    this.setData({ transcriptFocus: true, draftFocus: false });
  },

  focusDraft() {
    this.setData({ transcriptFocus: false, draftFocus: true });
  },

  async saveEdits(showToast) {
    if (this.data.loading || !this.data.contentReady) {
      return false;
    }
    const answerText = this.data.transcriptText.trim();
    const draftText = this.data.draftText.trim();
    if (!answerText || !draftText) {
      this.setData({ error: "回答和正文都不能为空" });
      return false;
    }
    this.setData({ saving: true, error: "" });
    try {
      const answer = await updateTextAnswer(this.data.answerId, answerText);
      const draft = await updateDraft(this.data.draftId, draftText);
      writeMemoirStore({ answer, draft });
      updateMemoirCachedStoryAnswer(answer);
      updateMemoirCachedStoryDraft(draft);
      if (showToast) {
        wx.showToast({ title: "已保存", icon: "success" });
      }
      return true;
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "保存失败" });
      return false;
    } finally {
      this.setData({ saving: false });
    }
  },

  async saveEditsOnly() {
    await this.saveEdits(true);
  },

  async saveAndContinue() {
    const saved = await this.saveEdits(false);
    if (!saved) {
      return;
    }
    this.setData({ saving: true, error: "" });
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
      this.setData({ saving: false });
    }
  },
});
