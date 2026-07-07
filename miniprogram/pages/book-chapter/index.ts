import type {
  MemoirBookChapterDraft,
  MemoirBookPlan,
  MemoirBookPlanStructureCommandRequest,
  MemoirBookSourceCoverage,
} from "../../domain/voice-chat";
import { definePage } from "../../services/page-definition";
import { trackAuditAction } from "../../services/audit-tracker";
import {
  applyMemoirBookPlanStructureCommand,
  confirmMemoirBookChapterDraft,
  enqueueMemoirBookChapterDraftGeneration,
  generateMemoirBookChapterTitle,
  getMemoirBookChapterDraft,
  getMemoirBookPlan,
  getMemoirBookSourceCoverage,
  updateMemoirBookChapterDraft,
  updateMemoirBookChapterTitle,
} from "../../services/memoir-voice-api";
import { ApiError } from "../../services/request";
import { routeAuthError } from "../../services/navigation";
import {
  datasetChoice,
  datasetString,
  pickerIndex,
  queryChoice,
  queryParam,
  type PageDatasetEvent,
  type PagePickerEvent,
  type PageQuery,
  type PageTextInputEvent,
} from "../../services/page-input";
import type { BookChapterView } from "../book/book-view-model";
import {
  buildChapterViews,
  latestDraftContent,
  trimText,
} from "../book/book-view-model";

const TAB_VALUES = ["content", "sources", "more"] as const;
const CHAPTER_MOVE_DIRECTIONS = ["up", "down"] as const;

type ChapterDetailTab = (typeof TAB_VALUES)[number];

interface ChapterDataset {
  tab?: string;
  direction?: string;
  utteranceId?: string;
}

type DatasetEvent = PageDatasetEvent<ChapterDataset>;
type PickerEvent = PagePickerEvent;
type TextInputEvent = PageTextInputEvent;

interface BookChapterPageData {
  loading: boolean;
  error: string;
  projectId: string;
  chapterId: string;
  activeTab: ChapterDetailTab;
  planTitle: string;
  plan: MemoirBookPlan | null;
  sourceCoverage: MemoirBookSourceCoverage | null;
  chapter: BookChapterView | null;
  chapterIndexText: string;
  contentText: string;
  sourceCountText: string;
  sourceEmptyText: string;
  titleBusy: boolean;
  updatingPlanStructure: boolean;
  splitSourceSelections: string[];
}

function chapterIndexText(plan: MemoirBookPlan | null, chapterId: string): string {
  const index = plan?.chapters
    .slice()
    .sort((left, right) => left.sort_order - right.sort_order)
    .findIndex((chapter) => chapter.id === chapterId) ?? -1;
  return index >= 0 ? `第 ${index + 1} 章` : "章节";
}

async function loadChapterDraft(projectId: string, chapterId: string): Promise<MemoirBookChapterDraft | null> {
  try {
    return await getMemoirBookChapterDraft(projectId, chapterId);
  } catch (error) {
    if (error instanceof ApiError && error.statusCode === 404) {
      return null;
    }
    throw error;
  }
}

async function loadSourceCoverage(projectId: string): Promise<MemoirBookSourceCoverage | null> {
  try {
    return await getMemoirBookSourceCoverage(projectId);
  } catch (error) {
    if (error instanceof ApiError && error.statusCode === 404) {
      return null;
    }
    throw error;
  }
}

definePage<BookChapterPageData>()({
  data: {
    loading: false,
    error: "",
    projectId: "",
    chapterId: "",
    activeTab: "content",
    planTitle: "",
    plan: null,
    sourceCoverage: null,
    chapter: null,
    chapterIndexText: "章节",
    contentText: "",
    sourceCountText: "0 条原始讲述",
    sourceEmptyText: "本章还没有关联原始讲述",
    titleBusy: false,
    updatingPlanStructure: false,
    splitSourceSelections: [],
  },

  async onLoad(query: PageQuery = {}) {
    const projectId = queryParam(query, "projectId");
    const chapterId = queryParam(query, "chapterId");
    const activeTab = queryChoice(query, "tab", TAB_VALUES, "content");
    this.setData({ projectId, chapterId, activeTab, error: "" });
    if (!projectId || !chapterId) {
      this.setData({ error: "缺少章节信息" });
      return;
    }
    await this.loadChapter(projectId, chapterId);
  },

  async loadChapter(projectId: string, chapterId: string) {
    if (this.data.loading) {
      return;
    }
    this.setData({ loading: true, error: "" });
    try {
      const [plan, coverage, draft] = await Promise.all([
        getMemoirBookPlan(projectId),
        loadSourceCoverage(projectId),
        loadChapterDraft(projectId, chapterId),
      ]);
      this.applyChapterData(plan, coverage, draft);
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "章节详情加载失败" });
    } finally {
      this.setData({ loading: false });
    }
  },

  applyChapterData(plan: MemoirBookPlan, coverage: MemoirBookSourceCoverage | null, draft: MemoirBookChapterDraft | null) {
    const chapters = buildChapterViews(
      plan,
      { [this.data.chapterId]: draft },
      coverage,
      { [this.data.chapterId]: this.data.splitSourceSelections },
    );
    const chapter = chapters.find((item) => item.id === this.data.chapterId) || null;
    if (!chapter) {
      this.setData({
        plan,
        sourceCoverage: coverage,
        chapter: null,
        planTitle: plan.title,
        chapterIndexText: "章节",
        contentText: "",
        sourceCountText: "0 条原始讲述",
        error: "没有找到这一章",
      });
      return;
    }
    const contentText = trimText(chapter.draftContent);
    this.setData({
      plan,
      sourceCoverage: coverage,
      planTitle: plan.title,
      chapter,
      chapterIndexText: chapterIndexText(plan, this.data.chapterId),
      contentText: contentText || "本章正文还没有生成",
      sourceCountText: `${chapter.sourceCount} 条原始讲述`,
      sourceEmptyText: "本章还没有关联原始讲述",
    });
  },

  patchChapter(patch: Partial<BookChapterView>) {
    if (!this.data.chapter) {
      return;
    }
    const chapter = { ...this.data.chapter, ...patch };
    const contentText = trimText(chapter.draftContent);
    this.setData({
      chapter,
      contentText: contentText || "本章正文还没有生成",
      sourceCountText: `${chapter.sourceCount} 条原始讲述`,
    });
  },

  patchDraft(draft: MemoirBookChapterDraft) {
    if (!this.data.plan) {
      return;
    }
    this.applyChapterData(this.data.plan, this.data.sourceCoverage, draft);
  },

  async applyPlanStructureCommand(command: MemoirBookPlanStructureCommandRequest, toastTitle: string) {
    if (!this.data.projectId || !this.data.plan || this.data.updatingPlanStructure) {
      return null;
    }
    if (this.data.chapter?.draftChanged) {
      this.patchChapter({ draftError: "请先保存当前修改，再调整章节结构" });
      return null;
    }
    this.setData({ updatingPlanStructure: true, error: "" });
    this.patchChapter({ draftError: "" });
    try {
      const plan = await applyMemoirBookPlanStructureCommand(this.data.projectId, command);
      wx.showToast({ title: toastTitle, icon: "success" });
      return plan;
    } catch (error) {
      if (routeAuthError(error, "me")) {
        return null;
      }
      this.setData({ error: error instanceof Error ? error.message : "目录调整失败" });
      return null;
    } finally {
      this.setData({ updatingPlanStructure: false });
    }
  },

  changeTab(event: DatasetEvent) {
    this.setData({ activeTab: datasetChoice(event, "tab", TAB_VALUES, "content") });
  },

  changeChapterTitle(event: TextInputEvent) {
    const chapter = this.data.chapter;
    if (!chapter) {
      return;
    }
    this.patchChapter({
      editingTitle: event.detail.value,
      titleChanged: trimText(event.detail.value) !== trimText(chapter.title),
      draftError: "",
    });
  },

  async saveChapterTitle() {
    const chapter = this.data.chapter;
    if (!chapter || !this.data.projectId || this.data.titleBusy || chapter.draftBusy) {
      return;
    }
    const title = trimText(chapter.editingTitle);
    if (!title) {
      this.patchChapter({ draftError: "章节名不能为空" });
      return;
    }
    if (title === trimText(chapter.title)) {
      this.patchChapter({ titleChanged: false, draftError: "" });
      wx.showToast({ title: "章名已保存", icon: "success" });
      return;
    }
    this.setData({ titleBusy: true, error: "" });
    this.patchChapter({ draftError: "" });
    try {
      const plan = await updateMemoirBookChapterTitle(this.data.projectId, this.data.chapterId, { title });
      this.applyChapterData(plan, this.data.sourceCoverage, chapter.draft);
      wx.showToast({ title: "章名已保存", icon: "success" });
    } catch (error) {
      if (routeAuthError(error, "me")) {
        return;
      }
      this.patchChapter({ draftError: error instanceof Error ? error.message : "章节名保存失败" });
    } finally {
      this.setData({ titleBusy: false });
    }
  },

  async generateChapterTitle() {
    const chapter = this.data.chapter;
    if (!chapter || !this.data.projectId || this.data.titleBusy || chapter.draftBusy) {
      return;
    }
    this.setData({ titleBusy: true, error: "" });
    this.patchChapter({ draftError: "" });
    try {
      const plan = await generateMemoirBookChapterTitle(this.data.projectId, this.data.chapterId, {});
      this.applyChapterData(plan, this.data.sourceCoverage, chapter.draft);
      trackAuditAction("book.chapter_title_generate", {}, { projectId: this.data.projectId });
      wx.showToast({ title: "已重新起名", icon: "success" });
    } catch (error) {
      if (routeAuthError(error, "me")) {
        return;
      }
      this.patchChapter({ draftError: error instanceof Error ? error.message : "小余起名失败" });
    } finally {
      this.setData({ titleBusy: false });
    }
  },

  changeDraftContent(event: TextInputEvent) {
    const chapter = this.data.chapter;
    if (!chapter) {
      return;
    }
    const value = event.detail.value;
    const original = latestDraftContent(chapter.draft);
    this.patchChapter({
      draftContent: value,
      draftCharCount: trimText(value).length,
      draftChanged: trimText(value) !== trimText(original),
      draftError: "",
    });
  },

  async generateChapterDraft() {
    const chapter = this.data.chapter;
    if (!chapter || !this.data.projectId || chapter.draftBusy) {
      return;
    }
    if (chapter.draftChanged) {
      this.patchChapter({ draftError: "请先保存当前修改，再生成新版本" });
      return;
    }
    this.patchChapter({ draftBusy: true, draftError: "" });
    try {
      const shouldSupplementPendingSources = chapter.pendingSourceCount > 0 && !chapter.removedSourceCount;
      const forceRegenerate = Boolean(chapter.draft?.latest_version && !shouldSupplementPendingSources);
      await enqueueMemoirBookChapterDraftGeneration(this.data.projectId, this.data.chapterId, {
        force_regenerate: forceRegenerate,
      });
      trackAuditAction(
        "book.chapter_draft_generate",
        { force_regenerate: forceRegenerate },
        { projectId: this.data.projectId },
      );
      wx.showToast({ title: "已后台生成", icon: "success" });
    } catch (error) {
      this.patchChapter({ draftError: error instanceof Error ? error.message : "章节稿生成失败" });
    } finally {
      this.patchChapter({ draftBusy: false });
    }
  },

  async saveChapterDraft() {
    const chapter = this.data.chapter;
    if (!chapter || !this.data.projectId || chapter.draftBusy) {
      return;
    }
    const content = trimText(chapter.draftContent);
    if (!content) {
      this.patchChapter({ draftError: "章节稿内容不能为空" });
      return;
    }
    this.patchChapter({ draftBusy: true, draftError: "" });
    try {
      const draft = await updateMemoirBookChapterDraft(this.data.projectId, this.data.chapterId, { content });
      this.patchDraft(draft);
      wx.showToast({ title: "章节稿已保存", icon: "success" });
    } catch (error) {
      this.patchChapter({ draftError: error instanceof Error ? error.message : "章节稿保存失败" });
    } finally {
      this.patchChapter({ draftBusy: false });
    }
  },

  async confirmChapterDraft() {
    const chapter = this.data.chapter;
    if (!chapter || !this.data.projectId || chapter.draftBusy || !chapter.draft?.latest_version) {
      return;
    }
    this.patchChapter({ draftBusy: true, draftError: "" });
    try {
      const draft = await confirmMemoirBookChapterDraft(this.data.projectId, this.data.chapterId);
      this.patchDraft(draft);
      trackAuditAction("book.chapter_draft_confirm", {}, { projectId: this.data.projectId });
      wx.showToast({ title: "本章已确认", icon: "success" });
    } catch (error) {
      this.patchChapter({ draftError: error instanceof Error ? error.message : "章节稿确认失败" });
    } finally {
      this.patchChapter({ draftBusy: false });
    }
  },

  async moveChapter(event: DatasetEvent) {
    const direction = datasetChoice(event, "direction", CHAPTER_MOVE_DIRECTIONS, "down");
    const chapter = this.data.chapter;
    if (!chapter || (direction === "up" && !chapter.canMoveUp) || (direction === "down" && !chapter.canMoveDown)) {
      return;
    }
    const plan = await this.applyPlanStructureCommand({
      type: "move_chapter",
      chapter_id: this.data.chapterId,
      direction,
    }, "章节顺序已调整");
    if (plan) {
      this.applyChapterData(plan, this.data.sourceCoverage, chapter.draft);
    }
  },

  async mergeChapterInto(event: PickerEvent) {
    const chapter = this.data.chapter;
    const optionIndex = pickerIndex(event);
    const option = chapter?.mergeTargetOptions[optionIndex];
    if (!chapter || !option || this.data.updatingPlanStructure) {
      return;
    }
    const confirmed = await new Promise<boolean>((resolve) => {
      wx.showModal({
        title: "合并章节",
        content: `把「${chapter.title}」合并到「${option.label}」，本章原始讲述会保留在合并后的章节中。`,
        confirmText: "合并",
        confirmColor: "#a64b3e",
        success: (result) => resolve(Boolean(result.confirm)),
        fail: () => resolve(false),
      });
    });
    if (!confirmed) {
      return;
    }
    const plan = await this.applyPlanStructureCommand({
      type: "merge_chapters",
      chapter_id: this.data.chapterId,
      target_chapter_id: option.value,
    }, "章节已合并");
    if (plan) {
      wx.navigateBack();
    }
  },

  toggleSplitSource(event: DatasetEvent) {
    const utteranceId = datasetString(event, "utteranceId");
    const chapter = this.data.chapter;
    if (!chapter || !utteranceId || chapter.source_refs.length <= 1 || this.data.updatingPlanStructure || chapter.draftBusy) {
      return;
    }
    const selected = new Set(this.data.splitSourceSelections);
    if (selected.has(utteranceId)) {
      selected.delete(utteranceId);
    } else {
      selected.add(utteranceId);
    }
    this.setData({ splitSourceSelections: Array.from(selected), error: "" });
    if (this.data.plan) {
      this.applyChapterData(this.data.plan, this.data.sourceCoverage, chapter.draft);
    }
  },

  async splitSelectedChapterSources() {
    const chapter = this.data.chapter;
    if (!chapter || !this.data.splitSourceSelections.length || this.data.updatingPlanStructure || chapter.draftBusy) {
      return;
    }
    if (this.data.splitSourceSelections.length >= chapter.source_refs.length) {
      this.patchChapter({ draftError: "拆分时至少保留 1 条原始讲述在原章节" });
      return;
    }
    const title = trimText(chapter.editingTitle || chapter.title) || "未命名章节";
    const plan = await this.applyPlanStructureCommand({
      type: "split_chapter",
      chapter_id: this.data.chapterId,
      title: `${title}（拆出）`,
      utterance_ids: this.data.splitSourceSelections,
    }, "章节已拆分");
    if (plan) {
      this.setData({ splitSourceSelections: [] });
      this.applyChapterData(plan, this.data.sourceCoverage, chapter.draft);
    }
  },

  async deleteChapter() {
    const chapter = this.data.chapter;
    const plan = this.data.plan;
    if (!chapter || !plan || plan.chapters.length <= 1 || this.data.updatingPlanStructure || chapter.draftBusy) {
      return;
    }
    const confirmed = await new Promise<boolean>((resolve) => {
      wx.showModal({
        title: "删除章节",
        content: `删除「${chapter.title}」会移除本章正文，收录讲述会回到待收录列表。`,
        confirmText: "删除",
        confirmColor: "#b84a5a",
        success: (result) => resolve(Boolean(result.confirm)),
        fail: () => resolve(false),
      });
    });
    if (!confirmed) {
      return;
    }
    const updatedPlan = await this.applyPlanStructureCommand({
      type: "delete_chapter",
      chapter_id: this.data.chapterId,
    }, "章节已删除");
    if (updatedPlan) {
      wx.navigateBack();
    }
  },

  goBack() {
    wx.navigateBack();
  },
});
