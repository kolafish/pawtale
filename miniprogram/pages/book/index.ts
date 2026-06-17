import type {
  MemoirBookChapterDraft,
  MemoirBookPlan,
  MemoirBookPlanChapter,
  MemoirBookPlanChapterInput,
} from "../../domain/voice-chat";
import { getMemoirIdentity } from "../../services/auth";
import {
  confirmMemoirBookChapterDraft,
  confirmMemoirBookPlan,
  generateMemoirBookChapterDraft,
  generateMemoirBookPlan,
  getMemoirBookChapterDraft,
  getMemoirBookPlan,
  updateMemoirBookChapterDraft,
} from "../../services/memoir-voice-api";
import { ApiError } from "../../services/request";

interface DatasetEvent {
  currentTarget: {
    dataset: {
      id?: string;
    };
  };
}

interface TextInputEvent extends DatasetEvent {
  detail: {
    value: string;
  };
}

type BookStage = "empty" | "plan_draft" | "drafting";

export interface BookChapterView extends MemoirBookPlanChapter {
  editingTitle: string;
  editingSummary: string;
  sourceCount: number;
  sourceSummary: string;
  draft: MemoirBookChapterDraft | null;
  draftContent: string;
  draftCharCount: number;
  draftChanged: boolean;
  draftStatusText: string;
  draftVersionText: string;
  draftBusy: boolean;
  draftError: string;
}

interface BookData {
  loading: boolean;
  generatingPlan: boolean;
  confirmingPlan: boolean;
  error: string;
  projectId: string;
  projectTitle: string;
  plan: MemoirBookPlan | null;
  planTitle: string;
  stage: BookStage;
  stageText: string;
  chapters: BookChapterView[];
  confirmedDraftCount: number;
  totalChapterCount: number;
  progressText: string;
}

function trimText(value: string): string {
  return value.trim();
}

function latestDraftContent(draft: MemoirBookChapterDraft | null): string {
  return draft?.latest_version?.content || "";
}

function draftStatusText(draft: MemoirBookChapterDraft | null): string {
  if (!draft?.latest_version) {
    return "未生成";
  }
  return draft.status === "confirmed" ? "已确认" : "待确认";
}

function draftVersionText(draft: MemoirBookChapterDraft | null): string {
  const version = draft?.latest_version?.version;
  return version ? `第 ${version} 版` : "暂无正文";
}

export function bookChapterSourceSummary(chapter: Pick<MemoirBookPlanChapter, "source_refs">): string {
  const quotes = chapter.source_refs
    .map((ref) => trimText(ref.quote || ""))
    .filter(Boolean)
    .slice(0, 2);
  if (!quotes.length) {
    return `${chapter.source_refs.length} 条来源转写`;
  }
  return quotes.join(" / ");
}

export function buildChapterViews(
  plan: MemoirBookPlan,
  draftsByChapterId: Record<string, MemoirBookChapterDraft | null> = {},
): BookChapterView[] {
  return [...plan.chapters]
    .sort((left, right) => left.sort_order - right.sort_order)
    .map((chapter) => {
      const draft = draftsByChapterId[chapter.id] || null;
      const content = latestDraftContent(draft);
      return {
        ...chapter,
        editingTitle: chapter.title,
        editingSummary: chapter.summary || "",
        sourceCount: chapter.source_refs.length,
        sourceSummary: bookChapterSourceSummary(chapter),
        draft,
        draftContent: content,
        draftCharCount: trimText(content).length,
        draftChanged: false,
        draftStatusText: draftStatusText(draft),
        draftVersionText: draftVersionText(draft),
        draftBusy: false,
        draftError: "",
      };
    });
}

export function bookProgressText(chapters: BookChapterView[]): string {
  if (!chapters.length) {
    return "还没有章节";
  }
  const confirmed = chapters.filter((chapter) => chapter.draft?.status === "confirmed").length;
  const generated = chapters.filter((chapter) => Boolean(chapter.draft?.latest_version)).length;
  return `${confirmed}/${chapters.length} 章已确认，${generated} 章已有正文`;
}

function planStage(plan: MemoirBookPlan | null): BookStage {
  if (!plan) {
    return "empty";
  }
  return plan.status === "confirmed" ? "drafting" : "plan_draft";
}

function stageText(stage: BookStage): string {
  if (stage === "empty") {
    return "等待生成章节";
  }
  if (stage === "plan_draft") {
    return "确认章节结构";
  }
  return "逐章成稿";
}

function toPlanInput(chapters: BookChapterView[]): MemoirBookPlanChapterInput[] {
  return chapters.map((chapter) => ({
    title: trimText(chapter.editingTitle),
    summary: trimText(chapter.editingSummary),
    source_refs: chapter.source_refs,
  }));
}

function isLoginError(error: unknown): boolean {
  return error instanceof ApiError && [401, 403].includes(error.statusCode);
}

Page<BookData, {
  onShow(): Promise<void>;
  loadBookHome(): Promise<void>;
  loadPlanForProject(projectId: string): Promise<void>;
  loadDraftsForPlan(plan: MemoirBookPlan): Promise<Record<string, MemoirBookChapterDraft | null>>;
  applyPlan(plan: MemoirBookPlan | null, draftsByChapterId?: Record<string, MemoirBookChapterDraft | null>): void;
  generatePlan(): Promise<void>;
  confirmPlan(): Promise<void>;
  changePlanTitle(event: TextInputEvent): void;
  changeChapterTitle(event: TextInputEvent): void;
  changeChapterSummary(event: TextInputEvent): void;
  removeChapter(event: DatasetEvent): void;
  generateChapterDraft(event: DatasetEvent): Promise<void>;
  changeDraftContent(event: TextInputEvent): void;
  saveChapterDraft(event: DatasetEvent): Promise<void>;
  confirmChapterDraft(event: DatasetEvent): Promise<void>;
  openTranscripts(): void;
  openHome(): void;
  patchChapter(chapterId: string, patch: Partial<BookChapterView>): void;
  patchDraft(chapterId: string, draft: MemoirBookChapterDraft): void;
}>({
  data: {
    loading: false,
    generatingPlan: false,
    confirmingPlan: false,
    error: "",
    projectId: "",
    projectTitle: "我的回忆录",
    plan: null,
    planTitle: "",
    stage: "empty",
    stageText: "等待生成章节",
    chapters: [],
    confirmedDraftCount: 0,
    totalChapterCount: 0,
    progressText: "还没有章节",
  },

  async onShow() {
    await this.loadBookHome();
  },

  async loadBookHome() {
    if (this.data.loading) {
      return;
    }
    this.setData({ loading: true, error: "" });
    try {
      const identity = await getMemoirIdentity();
      if (!identity.password_login_enabled) {
        wx.switchTab({ url: "/pages/me/index" });
        return;
      }
      const projectId = identity.active_project_id;
      const projectTitle = identity.active_project?.title || "我的回忆录";
      this.setData({ projectId, projectTitle });
      if (!projectId) {
        this.applyPlan(null);
        return;
      }
      await this.loadPlanForProject(projectId);
    } catch (error) {
      if (isLoginError(error)) {
        wx.switchTab({ url: "/pages/me/index" });
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "成书页面加载失败" });
    } finally {
      this.setData({ loading: false });
    }
  },

  async loadPlanForProject(projectId: string) {
    try {
      const plan = await getMemoirBookPlan(projectId);
      const draftsByChapterId = plan.status === "confirmed" ? await this.loadDraftsForPlan(plan) : {};
      this.applyPlan(plan, draftsByChapterId);
    } catch (error) {
      if (error instanceof ApiError && error.statusCode === 404) {
        this.applyPlan(null);
        return;
      }
      throw error;
    }
  },

  async loadDraftsForPlan(plan) {
    const entries = await Promise.all(
      plan.chapters.map(async (chapter) => {
        try {
          const draft = await getMemoirBookChapterDraft(plan.project_id, chapter.id);
          return [chapter.id, draft] as const;
        } catch (error) {
          if (error instanceof ApiError && error.statusCode === 404) {
            return [chapter.id, null] as const;
          }
          throw error;
        }
      }),
    );
    return entries.reduce<Record<string, MemoirBookChapterDraft | null>>((memo, [chapterId, draft]) => {
      memo[chapterId] = draft;
      return memo;
    }, {});
  },

  applyPlan(plan, draftsByChapterId = {}) {
    const chapters = plan ? buildChapterViews(plan, draftsByChapterId) : [];
    const stage = planStage(plan);
    const confirmedDraftCount = chapters.filter((chapter) => chapter.draft?.status === "confirmed").length;
    this.setData({
      plan,
      planTitle: plan?.title || `${this.data.projectTitle || "我的回忆录"}章节规划`,
      stage,
      stageText: stageText(stage),
      chapters,
      confirmedDraftCount,
      totalChapterCount: chapters.length,
      progressText: bookProgressText(chapters),
    });
  },

  async generatePlan() {
    if (!this.data.projectId || this.data.generatingPlan || this.data.loading) {
      return;
    }
    this.setData({ generatingPlan: true, error: "" });
    try {
      const plan = await generateMemoirBookPlan(this.data.projectId, { max_chapters: 8 });
      this.applyPlan(plan);
      wx.showToast({ title: "已生成章节", icon: "success" });
    } catch (error) {
      if (isLoginError(error)) {
        wx.switchTab({ url: "/pages/me/index" });
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "章节生成失败" });
    } finally {
      this.setData({ generatingPlan: false });
    }
  },

  async confirmPlan() {
    if (!this.data.projectId || this.data.confirmingPlan || !this.data.plan) {
      return;
    }
    const chapters = toPlanInput(this.data.chapters);
    if (!chapters.length) {
      this.setData({ error: "至少保留一个章节" });
      return;
    }
    const invalid = chapters.find((chapter) => !chapter.title || !chapter.source_refs.length);
    if (invalid) {
      this.setData({ error: "每个章节都需要标题和来源转写" });
      return;
    }
    this.setData({ confirmingPlan: true, error: "" });
    try {
      const plan = await confirmMemoirBookPlan(this.data.projectId, {
        title: trimText(this.data.planTitle) || `${this.data.projectTitle}章节规划`,
        chapters,
      });
      const draftsByChapterId = await this.loadDraftsForPlan(plan);
      this.applyPlan(plan, draftsByChapterId);
      wx.showToast({ title: "章节已确认", icon: "success" });
    } catch (error) {
      if (isLoginError(error)) {
        wx.switchTab({ url: "/pages/me/index" });
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "章节确认失败" });
    } finally {
      this.setData({ confirmingPlan: false });
    }
  },

  changePlanTitle(event) {
    this.setData({ planTitle: event.detail.value });
  },

  changeChapterTitle(event) {
    const id = event.currentTarget.dataset.id || "";
    this.patchChapter(id, { editingTitle: event.detail.value });
  },

  changeChapterSummary(event) {
    const id = event.currentTarget.dataset.id || "";
    this.patchChapter(id, { editingSummary: event.detail.value });
  },

  removeChapter(event) {
    const id = event.currentTarget.dataset.id || "";
    if (!id || this.data.confirmingPlan || this.data.chapters.length <= 1) {
      return;
    }
    const chapters = this.data.chapters.filter((chapter) => chapter.id !== id);
    this.setData({
      chapters,
      totalChapterCount: chapters.length,
      progressText: bookProgressText(chapters),
      error: "",
    });
  },

  async generateChapterDraft(event) {
    const chapterId = event.currentTarget.dataset.id || "";
    const chapter = this.data.chapters.find((candidate) => candidate.id === chapterId);
    if (!chapter || !this.data.projectId || chapter.draftBusy) {
      return;
    }
    if (chapter.draftChanged) {
      this.patchChapter(chapterId, { draftError: "请先保存当前修改，再重新生成" });
      return;
    }
    this.patchChapter(chapterId, { draftBusy: true, draftError: "" });
    try {
      const draft = await generateMemoirBookChapterDraft(this.data.projectId, chapterId, {
        force_regenerate: Boolean(chapter.draft?.latest_version),
      });
      this.patchDraft(chapterId, draft);
      wx.showToast({ title: "章节稿已生成", icon: "success" });
    } catch (error) {
      this.patchChapter(chapterId, {
        draftError: error instanceof Error ? error.message : "章节稿生成失败",
      });
    } finally {
      this.patchChapter(chapterId, { draftBusy: false });
    }
  },

  changeDraftContent(event) {
    const chapterId = event.currentTarget.dataset.id || "";
    const value = event.detail.value;
    const chapter = this.data.chapters.find((candidate) => candidate.id === chapterId);
    if (!chapter) {
      return;
    }
    const original = latestDraftContent(chapter.draft);
    this.patchChapter(chapterId, {
      draftContent: value,
      draftCharCount: trimText(value).length,
      draftChanged: trimText(value) !== trimText(original),
      draftError: "",
    });
  },

  async saveChapterDraft(event) {
    const chapterId = event.currentTarget.dataset.id || "";
    const chapter = this.data.chapters.find((candidate) => candidate.id === chapterId);
    if (!chapter || !this.data.projectId || chapter.draftBusy) {
      return;
    }
    const content = trimText(chapter.draftContent);
    if (!content) {
      this.patchChapter(chapterId, { draftError: "章节稿内容不能为空" });
      return;
    }
    this.patchChapter(chapterId, { draftBusy: true, draftError: "" });
    try {
      const draft = await updateMemoirBookChapterDraft(this.data.projectId, chapterId, { content });
      this.patchDraft(chapterId, draft);
      wx.showToast({ title: "章节稿已保存", icon: "success" });
    } catch (error) {
      this.patchChapter(chapterId, {
        draftError: error instanceof Error ? error.message : "章节稿保存失败",
      });
    } finally {
      this.patchChapter(chapterId, { draftBusy: false });
    }
  },

  async confirmChapterDraft(event) {
    const chapterId = event.currentTarget.dataset.id || "";
    const chapter = this.data.chapters.find((candidate) => candidate.id === chapterId);
    if (!chapter || !this.data.projectId || chapter.draftBusy || !chapter.draft?.latest_version) {
      return;
    }
    this.patchChapter(chapterId, { draftBusy: true, draftError: "" });
    try {
      const draft = await confirmMemoirBookChapterDraft(this.data.projectId, chapterId);
      this.patchDraft(chapterId, draft);
      wx.showToast({ title: "本章已确认", icon: "success" });
    } catch (error) {
      this.patchChapter(chapterId, {
        draftError: error instanceof Error ? error.message : "章节稿确认失败",
      });
    } finally {
      this.patchChapter(chapterId, { draftBusy: false });
    }
  },

  openTranscripts() {
    wx.switchTab({ url: "/pages/transcripts/index" });
  },

  openHome() {
    wx.switchTab({ url: "/pages/home/index" });
  },

  patchChapter(chapterId, patch) {
    if (!chapterId) {
      return;
    }
    const chapters = this.data.chapters.map((chapter) => (
      chapter.id === chapterId ? { ...chapter, ...patch } : chapter
    ));
    this.setData({
      chapters,
      confirmedDraftCount: chapters.filter((chapter) => chapter.draft?.status === "confirmed").length,
      totalChapterCount: chapters.length,
      progressText: bookProgressText(chapters),
    });
  },

  patchDraft(chapterId, draft) {
    const content = latestDraftContent(draft);
    this.patchChapter(chapterId, {
      draft,
      draftContent: content,
      draftCharCount: trimText(content).length,
      draftChanged: false,
      draftStatusText: draftStatusText(draft),
      draftVersionText: draftVersionText(draft),
      draftError: "",
    });
  },
});
