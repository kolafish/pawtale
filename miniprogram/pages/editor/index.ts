import { acceptDraft, updateDraft, updateTextAnswer } from "../../services/memoir";
import { readMemoirStore, writeMemoirStore } from "../../stores/session";

interface InputEvent {
  detail: {
    value: string;
  };
}

interface EditorData {
  answerId: string;
  draftId: string;
  transcriptText: string;
  draftText: string;
  saving: boolean;
  error: string;
}

Page<EditorData, {
  onLoad(query?: Record<string, string | undefined>): void;
  onTranscriptInput(event: InputEvent): void;
  onDraftInput(event: InputEvent): void;
  saveEdits(showToast: boolean): Promise<boolean>;
  saveEditsOnly(): Promise<void>;
  saveAndContinue(): Promise<void>;
}>({
  data: {
    answerId: "",
    draftId: "",
    transcriptText: "",
    draftText: "",
    saving: false,
    error: "",
  },

  onLoad(query) {
    const state = readMemoirStore();
    const answerId = query?.answerId || state.answer?.id || "";
    const draftId = query?.draftId || state.draft?.id || "";
    this.setData({
      answerId,
      draftId,
      transcriptText: state.answer?.transcript_edited || state.answer?.transcript_raw || "",
      draftText: state.draft?.content_edited || state.draft?.content_raw || "",
      error: !answerId || !draftId ? "缺少编辑内容" : "",
    });
  },

  onTranscriptInput(event) {
    this.setData({ transcriptText: event.detail.value });
  },

  onDraftInput(event) {
    this.setData({ draftText: event.detail.value });
  },

  async saveEdits(showToast) {
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
      wx.redirectTo({ url: "/pages/question/index" });
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "确认失败" });
    } finally {
      this.setData({ saving: false });
    }
  },
});
