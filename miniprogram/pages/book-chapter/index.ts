import type {
  MemoirBookChapterDraft,
  MemoirBookPlan,
  MemoirBookSourceCoverage,
} from "../../domain/voice-chat";
import { trackAuditAction } from "../../services/audit-tracker";
import {
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
import type { BookChapterView } from "../book/book-view-model";
import {
  buildChapterViews,
  latestDraftContent,
  trimText,
} from "../book/book-view-model";

type ChapterDetailTab = "content" | "sources" | "more";

interface PageQuery {
  projectId?: string;
  chapterId?: string;
  tab?: string;
}

interface DatasetEvent {
  currentTarget: {
    dataset: {
      tab?: string;
    };
  };
}

interface TextInputEvent {
  detail: {
    value: string;
  };
}

interface ChapterMoreActionView {
  id: string;
  title: string;
  detailText: string;
}

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
  moreActions: ChapterMoreActionView[];
}

const TAB_VALUES: ChapterDetailTab[] = ["content", "sources", "more"];

function decodeQueryValue(value = ""): string {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

function normalizeTab(value = ""): ChapterDetailTab {
  const tab = decodeQueryValue(value) as ChapterDetailTab;
  return TAB_VALUES.includes(tab) ? tab : "content";
}

function chapterIndexText(plan: MemoirBookPlan | null, chapterId: string): string {
  const index = plan?.chapters
    .slice()
    .sort((left, right) => left.sort_order - right.sort_order)
    .findIndex((chapter) => chapter.id === chapterId) ?? -1;
  return index >= 0 ? `第 ${index + 1} 章` : "章节";
}

function moreActionsForChapter(chapter: BookChapterView): ChapterMoreActionView[] {
  return [
    {
      id: "history",
      title: "历史版本",
      detailText: chapter.draftVersionText || "查看本章历史版本",
    },
    {
      id: "move",
      title: "移动章节顺序",
      detailText: "调整这一章在全书里的位置",
    },
    {
      id: "merge",
      title: "合并到其他章节",
      detailText: "把这一章并入相邻或指定章节",
    },
    {
      id: "split",
      title: "拆分原始讲述",
      detailText: chapter.sourceCount ? `从 ${chapter.sourceCount} 条原始讲述里拆出新章` : "本章暂无原始讲述",
    },
  ];
}

function isLoginError(error: unknown): boolean {
  return error instanceof ApiError && [401, 403].includes(error.statusCode);
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

Page<BookChapterPageData, {
  onLoad(query?: PageQuery): Promise<void>;
  loadChapter(projectId: string, chapterId: string): Promise<void>;
  applyChapterData(
    plan: MemoirBookPlan,
    coverage: MemoirBookSourceCoverage | null,
    draft: MemoirBookChapterDraft | null,
  ): void;
  patchChapter(patch: Partial<BookChapterView>): void;
  patchDraft(draft: MemoirBookChapterDraft): void;
  changeTab(event: DatasetEvent): void;
  changeChapterTitle(event: TextInputEvent): void;
  saveChapterTitle(): Promise<void>;
  generateChapterTitle(): Promise<void>;
  changeDraftContent(event: TextInputEvent): void;
  generateChapterDraft(): Promise<void>;
  saveChapterDraft(): Promise<void>;
  confirmChapterDraft(): Promise<void>;
  goBack(): void;
}>({
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
    moreActions: [],
  },

  async onLoad(query = {}) {
    const projectId = decodeQueryValue(query.projectId || "");
    const chapterId = decodeQueryValue(query.chapterId || "");
    const activeTab = normalizeTab(query.tab || "");
    this.setData({ projectId, chapterId, activeTab, error: "" });
    if (!projectId || !chapterId) {
      this.setData({ error: "缺少章节信息" });
      return;
    }
    await this.loadChapter(projectId, chapterId);
  },

  async loadChapter(projectId, chapterId) {
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

  applyChapterData(plan, coverage, draft) {
    const chapters = buildChapterViews(plan, { [this.data.chapterId]: draft }, coverage);
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
        moreActions: [],
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
      moreActions: moreActionsForChapter(chapter),
    });
  },

  patchChapter(patch) {
    if (!this.data.chapter) {
      return;
    }
    const chapter = { ...this.data.chapter, ...patch };
    const contentText = trimText(chapter.draftContent);
    this.setData({
      chapter,
      contentText: contentText || "本章正文还没有生成",
      sourceCountText: `${chapter.sourceCount} 条原始讲述`,
      moreActions: moreActionsForChapter(chapter),
    });
  },

  patchDraft(draft) {
    if (!this.data.plan) {
      return;
    }
    this.applyChapterData(this.data.plan, this.data.sourceCoverage, draft);
  },

  changeTab(event) {
    const tab = String(event.currentTarget.dataset.tab || "");
    this.setData({ activeTab: normalizeTab(tab) });
  },

  changeChapterTitle(event) {
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
      if (isLoginError(error)) {
        wx.switchTab({ url: "/pages/me/index" });
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
      if (isLoginError(error)) {
        wx.switchTab({ url: "/pages/me/index" });
        return;
      }
      this.patchChapter({ draftError: error instanceof Error ? error.message : "AI 起名失败" });
    } finally {
      this.setData({ titleBusy: false });
    }
  },

  changeDraftContent(event) {
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

  goBack() {
    wx.navigateBack();
  },
});
