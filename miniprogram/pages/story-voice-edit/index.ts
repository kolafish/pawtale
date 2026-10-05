import { accessibilityPageClass } from "../../services/accessibility-settings";
import { definePage } from "../../services/page-definition";
import { pageUrl, queryParam, type PageQuery } from "../../services/page-input";
import { ensureMemoirBackendSession, memoirProjectDictationUrl } from "../../services/memoir-api-client";
import { getMemoirChapterSectionRegenerationJob, type MemoirChapterSectionRegenerationJob } from "../../services/memoir-chapter-workspace-api";
import { applyVoiceEdit, previewVoiceEdit, type VoiceEditItem, type VoiceEditPreview } from "../../services/memoir-voice-edit";
import { PollingTimer } from "../../services/polling-timer";
import { markCurrentUserMemoirSnapshotsStale } from "../../stores/memoir-read-cache";

type SectionTaskStatus = MemoirChapterSectionRegenerationJob["status"] | "checking";

interface SectionTask {
  chapterId: string;
  label: string;
  status: SectionTaskStatus;
  detail: string;
}

function sectionTaskResult(task: SectionTask, job: MemoirChapterSectionRegenerationJob): SectionTask {
  let detail: string;
  if (job.status === "succeeded") {
    detail = Number.isInteger(job.section_count)
      ? `已整理为 ${job.section_count} 个小节。`
      : "整理已完成，请进入本章查看最终小节数。";
  } else if (job.status === "failed") {
    detail = `原有小节保持不变。${job.error_message || "请稍后重试。"}`;
  } else if (job.status === "cancelled") {
    detail = "已取消，原有小节保持不变。";
  } else if (job.status === "idle") {
    detail = "未找到本次整理任务，请进入本章查看。";
  } else {
    detail = job.status === "pending"
      ? "任务已排队，完成前书稿的小节数不会变化。"
      : "正在整理小节，完成前书稿的小节数不会变化。";
  }
  return { ...task, status: job.status, detail };
}

function taskNeedsRefresh(task: SectionTask): boolean {
  return task.status === "checking" || task.status === "pending" || task.status === "running";
}

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
  sectionTasks: SectionTask[];
  preview: VoiceEditPreview | null;
  splitConfirmation: VoiceEditItem | null;
  items: Array<VoiceEditItem & { selected: boolean }>;
  selectedCount: number;
}

definePage<EditData>()({
  data: {
    pageClass: accessibilityPageClass("voice-edit-page ui-page-shell ui-accessible-page"),
    projectId: "", projectTitle: "", gatewayUrl: "", instruction: "",
    loading: false, applying: false, error: "", message: "", sectionTasks: [], preview: null, splitConfirmation: null, items: [], selectedCount: 0,
  },
  requestSequence: 0,
  sectionTaskSequence: 0,
  sectionTaskPoller: new PollingTimer(3000),
  pageVisible: false,

  onLoad(query: PageQuery) {
    this.pageVisible = true;
    const projectId = queryParam(query, "projectId");
    this.setData({ projectId, projectTitle: queryParam(query, "projectTitle"), gatewayUrl: memoirProjectDictationUrl(projectId) });
    if (!projectId) this.setData({ error: "请从当前回忆录的首页进入语音编辑" });
  },

  onShow() {
    this.pageVisible = true;
    this.setData({ pageClass: accessibilityPageClass("voice-edit-page ui-page-shell ui-accessible-page") });
    if (this.data.sectionTasks.some(taskNeedsRefresh)) void this.refreshSectionTasks();
  },

  onHide() {
    this.pageVisible = false;
    this.requestSequence++;
    this.sectionTaskSequence++;
    this.sectionTaskPoller.cancel();
    if (this.data.sectionTasks.length) markCurrentUserMemoirSnapshotsStale();
    this.setData({ loading: false, applying: false, preview: null, splitConfirmation: null, items: [], selectedCount: 0 });
  },

  onUnload() { this.onHide(); },

  changeInstruction(event: { detail: { value: string } }) {
    if (this.data.loading || this.data.applying) return;
    const clarification = this.data.preview?.questions.length && !this.data.items.length
      ? this.data.preview : null;
    this.setData({ instruction: event.detail.value, preview: clarification, splitConfirmation: null, items: [], selectedCount: 0, message: "", error: "" });
  },

  async submitInstruction(event?: { detail?: { value?: string } }) {
    const instruction = String(event?.detail?.value ?? this.data.instruction).trim();
    if (!instruction || !this.data.projectId || this.data.loading || this.data.applying) return;
    const sequence = ++this.requestSequence;
    this.setData({ instruction, loading: true, error: "", message: "", preview: null, splitConfirmation: null, items: [], selectedCount: 0 });
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
    this.setData({ preview: null, splitConfirmation: null, items: [], selectedCount: 0, message: "", error: "" });
  },

  async apply() {
    if (!this.data.preview || this.data.applying || !this.data.selectedCount) return;
    const sequence = ++this.requestSequence;
    const selectedItems = this.data.items.filter(item => item.selected);
    const operations = selectedItems.map(({ action, target_ref, new_title, utterance_ids, section_ids }) => ({
      action, target_ref, new_title,
      ...((action === "split_chapter" || action === "merge_sections") && section_ids?.length ? { section_ids } : {}),
      ...(action === "split_chapter" && utterance_ids?.length ? { utterance_ids } : {}),
    }));
    this.setData({ applying: true, error: "" });
    try {
      const result = await applyVoiceEdit(this.data.projectId, this.data.preview.signature, operations);
      if (sequence !== this.requestSequence) return;
      const sectionTasks: SectionTask[] = selectedItems
        .filter(item => item.action === "regenerate_chapter_sections" || item.action === "consolidate_chapter_sections")
        .map(item => ({ chapterId: item.target_ref, label: item.label, status: "checking", detail: "正在读取本章整理进度…" }));
      this.sectionTaskPoller.cancel();
      this.sectionTaskSequence++;
      this.setData({
        message: sectionTasks.length
          ? `${operations.length > sectionTasks.length ? "其他修改已保存；" : ""}${sectionTasks.length === 1 ? "本章" : `${sectionTasks.length} 章`}的小节整理任务已开始，下面分别显示处理结果。`
          : result.message,
        sectionTasks,
        preview: null, splitConfirmation: null, items: [], selectedCount: 0, instruction: "",
      });
      if (sectionTasks.length) void this.refreshSectionTasks();
    } catch (error) {
      if (sequence !== this.requestSequence) return;
      // A lost response may follow a successful commit. Do not replay the old batch.
      this.setData({ preview: null, splitConfirmation: null, items: [], selectedCount: 0, error: `${error instanceof Error ? error.message : "未收到修改结果"}。请先回到故事查看当前内容，再提出修改。` });
    } finally {
      if (sequence === this.requestSequence) this.setData({ applying: false });
    }
  },

  async refreshSectionTasks() {
    if (!this.pageVisible || !this.data.projectId) return;
    const sequence = this.sectionTaskSequence;
    const tasks = this.data.sectionTasks.filter(taskNeedsRefresh);
    if (!tasks.length) return;
    const updates = await Promise.all(tasks.map(async task => {
      try {
        const job = await getMemoirChapterSectionRegenerationJob(this.data.projectId, task.chapterId);
        return sectionTaskResult(task, job);
      } catch (error) {
        return { ...task, status: "checking" as const, detail: error instanceof Error
          ? `进度暂时读取失败：${error.message}。正在重试。`
          : "进度暂时读取失败，正在重试。" };
      }
    }));
    if (!this.pageVisible || sequence !== this.sectionTaskSequence) return;
    const byChapterId = new Map(updates.map(task => [task.chapterId, task]));
    const sectionTasks = this.data.sectionTasks.map(task => byChapterId.get(task.chapterId) || task);
    if (this.data.sectionTasks.some((task, index) => taskNeedsRefresh(task) && !taskNeedsRefresh(sectionTasks[index]))) {
      markCurrentUserMemoirSnapshotsStale();
    }
    this.setData({ sectionTasks });
    if (sectionTasks.some(taskNeedsRefresh)) {
      this.sectionTaskPoller.schedule(() => { void this.refreshSectionTasks(); });
    } else {
      this.sectionTaskPoller.cancel();
    }
  },

  openChapterProgress(event: { currentTarget: { dataset: { chapterId?: string } } }) {
    const chapterId = event.currentTarget.dataset.chapterId;
    if (!this.data.projectId || !chapterId) return;
    wx.navigateTo({ url: pageUrl("/pages/chapter-workspace/index", {
      projectId: this.data.projectId,
      chapterId,
    }) });
  },

  backToStory() { wx.navigateBack({ delta: 1 }); },
});
