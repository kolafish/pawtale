import { accessibilityPageClass } from "../../services/accessibility-settings";
import { definePage } from "../../services/page-definition";
import { queryParam, type PageQuery } from "../../services/page-input";
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
  preview: VoiceEditPreview | null;
  items: Array<VoiceEditItem & { selected: boolean }>;
  selectedCount: number;
}

definePage<EditData>()({
  data: {
    pageClass: accessibilityPageClass("voice-edit-page ui-page-shell ui-accessible-page"),
    projectId: "", projectTitle: "", gatewayUrl: "", instruction: "",
    loading: false, applying: false, error: "", message: "", preview: null, items: [], selectedCount: 0,
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
    this.setData({ loading: false, applying: false, preview: null, items: [], selectedCount: 0 });
  },

  onUnload() { this.onHide(); },

  changeInstruction(event: { detail: { value: string } }) {
    if (this.data.loading || this.data.applying) return;
    this.setData({ instruction: event.detail.value, preview: null, items: [], selectedCount: 0, message: "", error: "" });
  },

  async submitInstruction() {
    const instruction = this.data.instruction.trim();
    if (!instruction || !this.data.projectId || this.data.loading || this.data.applying) return;
    const sequence = ++this.requestSequence;
    this.setData({ loading: true, error: "", message: "", preview: null, items: [], selectedCount: 0 });
    try {
      await ensureMemoirBackendSession();
      if (sequence !== this.requestSequence) return;
      const preview = await previewVoiceEdit(this.data.projectId, instruction);
      if (sequence !== this.requestSequence) return;
      this.setData({ preview, items: preview.operations.map(item => ({ ...item, selected: true })), selectedCount: preview.operations.length });
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

  async apply() {
    if (!this.data.preview || this.data.applying || !this.data.selectedCount) return;
    const sequence = ++this.requestSequence;
    const operations = this.data.items.filter(item => item.selected).map(({ action, target_ref, new_title }) => ({ action, target_ref, new_title }));
    this.setData({ applying: true, error: "" });
    try {
      const result = await applyVoiceEdit(this.data.projectId, this.data.preview.signature, operations);
      if (sequence !== this.requestSequence) return;
      this.setData({ message: result.message, preview: null, items: [], selectedCount: 0, instruction: "" });
    } catch (error) {
      if (sequence !== this.requestSequence) return;
      // A lost response may follow a successful commit. Do not replay the old batch.
      this.setData({ preview: null, items: [], selectedCount: 0, error: `${error instanceof Error ? error.message : "未收到修改结果"}。请先回到故事查看当前内容，再提出修改。` });
    } finally {
      if (sequence === this.requestSequence) this.setData({ applying: false });
    }
  },

  backToStory() { wx.navigateBack({ delta: 1 }); },
});
