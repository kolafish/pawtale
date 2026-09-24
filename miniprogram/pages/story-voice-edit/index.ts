import { accessibilityPageClass } from "../../services/accessibility-settings";
import { definePage } from "../../services/page-definition";
import { pageUrl, queryParam, type PageQuery } from "../../services/page-input";
import { ensureMemoirBackendSession, memoirProjectDictationUrl } from "../../services/memoir-api-client";
import { applyVoiceEdit, previewVoiceEdit, type VoiceEditItem, type VoiceEditPreview } from "../../services/memoir-voice-edit";

interface EditData {
  pageClass: string;
  projectId: string;
  projectTitle: string;
  gatewayUrl: string;
  instruction: string;
  loading: boolean;
  applying: boolean;
  error: string;
  message: string;
  progressChapterId: string;
  preview: VoiceEditPreview | null;
  splitConfirmation: VoiceEditItem | null;
  items: Array<VoiceEditItem & { selected: boolean }>;
  selectedCount: number;
}

definePage<EditData>()({
  data: {
    pageClass: accessibilityPageClass("voice-edit-page ui-page-shell ui-accessible-page"),
    projectId: "", projectTitle: "", gatewayUrl: "", instruction: "",
    loading: false, applying: false, error: "", message: "", progressChapterId: "", preview: null, splitConfirmation: null, items: [], selectedCount: 0,
  },
  requestSequence: 0,

  onLoad(query: PageQuery) {
    const projectId = queryParam(query, "projectId");
    this.setData({ projectId, projectTitle: queryParam(query, "projectTitle"), gatewayUrl: memoirProjectDictationUrl(projectId) });
    if (!projectId) this.setData({ error: "请从当前回忆录的首页进入语音编辑" });
  },

  onShow() {
    this.setData({ pageClass: accessibilityPageClass("voice-edit-page ui-page-shell ui-accessible-page") });
  },

  onHide() {
    this.requestSequence++;
    this.setData({ loading: false, applying: false, preview: null, splitConfirmation: null, items: [], selectedCount: 0 });
  },

  onUnload() { this.onHide(); },

  changeInstruction(event: { detail: { value: string } }) {
    if (this.data.loading || this.data.applying) return;
    const clarification = this.data.preview?.questions.length && !this.data.items.length
      ? this.data.preview : null;
    this.setData({ instruction: event.detail.value, preview: clarification, splitConfirmation: null, items: [], selectedCount: 0, message: "", progressChapterId: "", error: "" });
  },

  async submitInstruction(event?: { detail?: { value?: string } }) {
    const instruction = String(event?.detail?.value ?? this.data.instruction).trim();
    if (!instruction || !this.data.projectId || this.data.loading || this.data.applying) return;
    const sequence = ++this.requestSequence;
    this.setData({ instruction, loading: true, error: "", message: "", progressChapterId: "", preview: null, splitConfirmation: null, items: [], selectedCount: 0 });
    try {
      await ensureMemoirBackendSession();
      if (sequence !== this.requestSequence) return;
      const preview = await previewVoiceEdit(this.data.projectId, instruction);
      if (sequence !== this.requestSequence) return;
      const splitConfirmation = preview.questions.length === 0 && preview.operations.length === 1
        && preview.operations[0].action === "split_chapter" && preview.operations[0].section_ids?.length
        ? preview.operations[0] : null;
      this.setData({ preview, splitConfirmation, items: preview.operations.map(item => ({ ...item, selected: true })), selectedCount: preview.operations.length });
    } catch (error) {
      if (sequence === this.requestSequence) this.setData({ error: error instanceof Error ? error.message : "没有得到修改结果，请重试" });
    } finally {
      if (sequence === this.requestSequence) this.setData({ loading: false });
    }
  },

  selectOperations(event: { detail: { value: string[] } }) {
    if (this.data.applying) return;
    const selected = new Set(event.detail.value);
    const items = this.data.items.map(item => ({ ...item, selected: selected.has(item.target_ref) }));
    this.setData({ items, selectedCount: items.filter(item => item.selected).length });
  },

  restartInstruction() {
    if (this.data.loading || this.data.applying) return;
    this.requestSequence++;
    this.setData({ preview: null, splitConfirmation: null, items: [], selectedCount: 0, message: "", progressChapterId: "", error: "" });
  },

  async apply() {
    if (!this.data.preview || this.data.applying || !this.data.selectedCount) return;
    const sequence = ++this.requestSequence;
    const operations = this.data.items.filter(item => item.selected).map(({ action, target_ref, new_title, utterance_ids, section_ids }) => ({
      action, target_ref, new_title,
      ...(action === "split_chapter" && section_ids?.length ? { section_ids } : {}),
      ...(action === "split_chapter" && utterance_ids?.length ? { utterance_ids } : {}),
    }));
    this.setData({ applying: true, error: "" });
    try {
      const result = await applyVoiceEdit(this.data.projectId, this.data.preview.signature, operations);
      if (sequence !== this.requestSequence) return;
      const regeneration = operations.find(item => item.action === "regenerate_chapter_sections");
      this.setData({
        message: regeneration
          ? `${operations.length > 1 ? "修改已保存，" : ""}已开始重新整理小节。`
          : result.message,
        progressChapterId: regeneration?.target_ref || "",
        preview: null, splitConfirmation: null, items: [], selectedCount: 0, instruction: "",
      });
    } catch (error) {
      if (sequence !== this.requestSequence) return;
      // A lost response may follow a successful commit. Do not replay the old batch.
      this.setData({ preview: null, splitConfirmation: null, items: [], selectedCount: 0, progressChapterId: "", error: `${error instanceof Error ? error.message : "未收到修改结果"}。请先回到故事查看当前内容，再提出修改。` });
    } finally {
      if (sequence === this.requestSequence) this.setData({ applying: false });
    }
  },

  openChapterProgress() {
    if (!this.data.projectId || !this.data.progressChapterId) return;
    wx.navigateTo({ url: pageUrl("/pages/chapter-workspace/index", {
      projectId: this.data.projectId,
      chapterId: this.data.progressChapterId,
    }) });
  },

  backToStory() { wx.navigateBack({ delta: 1 }); },
});
