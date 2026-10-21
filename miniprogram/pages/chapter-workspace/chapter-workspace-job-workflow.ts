import { PollingTimer } from "../../services/polling-timer";
import {
  cancelMemoirChapterPreparationJob,
  acceptMemoirChapterCandidate,
  enqueueMemoirChapterPreparationJob,
  getMemoirChapterPreparationJob,
  type MemoirChapterPreparationJob,
} from "../../services/memoir-book-completion-api";
import {
  cancelMemoirChapterSectionRegenerationJob,
  getMemoirChapterSectionRegenerationJob,
  mergeMemoirChapterSections,
  regenerateMemoirChapterSections,
  type MemoirChapterSectionRegenerationJob,
} from "../../services/memoir-chapter-workspace-api";
import { routeAuthError } from "../../services/navigation";
import {
  datasetString,
  type PageDatasetEvent,
} from "../../services/page-input";
import {
  chapterCandidateViewPatch,
  chapterWorkspaceViewPatch,
} from "./chapter-workspace-view";
import {
  chapterPreparationView,
  isActiveChapterPreparation,
  isActiveSectionRegeneration,
  scrollToSection,
  sectionRegenerationView,
  type ChapterWorkspaceOperationKind,
  type ChapterWorkspacePageData,
} from "./chapter-workspace-model";

interface ChapterWorkspaceJobHost {
  data: ChapterWorkspacePageData;
  pageVisible: boolean;
  chapterPreparationLoadSequence: number;
  sectionRegenerationLoadSequence: number;
  preparationPoller: PollingTimer;
  sectionRegenerationPoller: PollingTimer;
  setData(patch: Partial<ChapterWorkspacePageData>): void;
  beginOperation(kind: ChapterWorkspaceOperationKind, targetId?: string): boolean;
  finishOperation(kind: ChapterWorkspaceOperationKind, targetId?: string): void;
  loadPageData(silent?: boolean): Promise<void>;
}

function defineChapterWorkspaceJobMethods<TMethods extends object>(
  methods: TMethods & ThisType<ChapterWorkspaceJobHost & TMethods>,
): TMethods {
  return methods;
}

export const chapterWorkspaceJobMethods = defineChapterWorkspaceJobMethods({
  toggleEditingTools() {
    if (this.data.operationKind || this.data.editingSectionId) {
      return;
    }
    this.setData({
      editingToolsExpanded: !this.data.editingToolsExpanded,
    });
  },

  switchCandidateView(event: PageDatasetEvent<{ mode?: string; version?: string }>) {
    const mode = datasetString(event, "mode");
    const version = datasetString(event, "version");
    if (!this.data.workspace) {
      return;
    }
    this.setData(chapterCandidateViewPatch(
      this.data.workspace,
      this.data.chapterPreparationJob,
      mode === "changes" || mode === "whole" ? mode : this.data.candidateMode,
      version === "before" || version === "after"
        ? version
        : this.data.candidatePreviewVersion,
    ));
  },

  toggleSection(event: PageDatasetEvent<{ id?: string }>) {
    const sectionId = datasetString(event, "id");
    const workspace = this.data.workspace;
    if (!sectionId || !workspace) {
      return;
    }
    const currentExpanded = this.data.sectionViews.find(
      (section) => section.id === sectionId,
    )?.expanded || false;
    const expandedSectionIds = {
      ...this.data.expandedSectionIds,
      [sectionId]: !currentExpanded,
    };
    this.setData({
      expandedSectionIds,
      ...chapterWorkspaceViewPatch(
        workspace,
        expandedSectionIds,
      ),
    });
  },

  regenerateSections() {
    const workspace = this.data.workspace;
    if (
      !workspace?.sections.length
      || Boolean(this.data.operationKind)
      || Boolean(this.data.editingSectionId)
    ) {
      return;
    }
    wx.showModal({
      title: "重新整理小节分组",
      content: "AI 会重新划分本章回忆的分组和顺序。原始回忆、是否入书的选择和现有正文版本都会保留，完成后需要按新结构重新生成正文。",
      confirmText: "开始整理",
      cancelText: "暂不整理",
      success: (result) => {
        if (result.confirm) {
          void this.performSectionRegeneration();
        }
      },
    });
  },

  mergeSectionIntoPrevious(
    event: PageDatasetEvent<{
      sourceId?: string;
      sourceTitle?: string;
      targetId?: string;
      targetTitle?: string;
    }>,
  ) {
    const sourceId = datasetString(event, "sourceId");
    const sourceTitle = datasetString(event, "sourceTitle");
    const targetId = datasetString(event, "targetId");
    const targetTitle = datasetString(event, "targetTitle");
    if (
      !sourceId
      || !targetId
      || Boolean(this.data.operationKind)
      || Boolean(this.data.editingSectionId)
    ) {
      return;
    }
    wx.showModal({
      title: "与上一小节合并？",
      content: `“${sourceTitle}”中的回忆会并入“${targetTitle}”。现有正文会保留为历史版本，合并后需要重新生成本章正文并再次确认。`,
      confirmText: "确认合并",
      cancelText: "暂不合并",
      success: (result) => {
        if (result.confirm) {
          void this.performSectionMerge(sourceId, targetId);
        }
      },
    });
  },

  async performSectionMerge(sourceId: string, targetId: string) {
    if (!this.beginOperation("merge_sections", sourceId)) {
      return;
    }
    try {
      await mergeMemoirChapterSections(
        this.data.projectId,
        targetId,
        sourceId,
      );
      this.setData({
        expandedSectionIds: {
          ...this.data.expandedSectionIds,
          [targetId]: true,
        },
      });
      await this.loadPageData(true);
      wx.showToast({ title: "小节已合并", icon: "success" });
    } catch (error) {
      if (routeAuthError(error, "login")) {
        return;
      }
      this.setData({
        error: error instanceof Error ? error.message : "小节合并失败",
      });
    } finally {
      this.finishOperation("merge_sections", sourceId);
    }
  },

  async performSectionRegeneration() {
    const workspace = this.data.workspace;
    if (
      !workspace?.sections.length
      || !this.beginOperation("regenerate_sections")
    ) {
      return;
    }
    try {
      const job = await regenerateMemoirChapterSections(
        this.data.projectId,
        this.data.chapterId,
        workspace.sections.map((section) => ({
          id: section.id,
          version: section.version,
        })),
      );
      await this.applySectionRegenerationJob(job);
    } catch (error) {
      if (routeAuthError(error, "login")) {
        return;
      }
      this.finishOperation("regenerate_sections");
      this.setData({
        error: error instanceof Error ? error.message : "小节重新整理失败",
      });
    }
  },

  async refreshChapterPreparationJob(initial = false) {
    if (!this.data.projectId || !this.data.chapterId) {
      return;
    }
    const loadSequence = ++this.chapterPreparationLoadSequence;
    try {
      const job = await getMemoirChapterPreparationJob(
        this.data.projectId,
        this.data.chapterId,
      );
      if (
        !this.pageVisible
        || loadSequence !== this.chapterPreparationLoadSequence
      ) {
        return;
      }
      await this.applyChapterPreparationJob(job, initial);
    } catch (error) {
      if (routeAuthError(error, "login")) {
        return;
      }
      if (loadSequence !== this.chapterPreparationLoadSequence) {
        return;
      }
      if (!initial) {
        this.setData({
          error: error instanceof Error ? error.message : "章节处理进度读取失败",
        });
        this.schedulePreparationPolling();
      }
    }
  },

  async applyChapterPreparationJob(
    job: MemoirChapterPreparationJob,
    initial = false,
  ) {
    const previous = this.data.chapterPreparationJob;
    const wasActive = (
      previous?.job_id === job.job_id
      && isActiveChapterPreparation(previous)
    );
    if (isActiveChapterPreparation(job)) {
      const view = chapterPreparationView(job);
      this.setData({
        operationKind: job.operation === "rewrite"
          ? "rewrite_chapter"
          : "prepare",
        operationTargetId: "",
        chapterPreparationJob: job,
        chapterPreparationTitle: view.title,
        chapterPreparationDetail: view.detail,
        chapterPreparationProgress: view.progress,
        chapterPreparationProgressMode: view.progressMode,
        error: "",
      });
      this.schedulePreparationPolling();
      return;
    }

    this.stopPreparationPolling();
    this.finishOperation(
      job.operation === "rewrite" ? "rewrite_chapter" : "prepare",
    );
    if (job.status === "succeeded") {
      if (job.outcome === "needs_review" && job.candidate_content) {
        this.setData({
          chapterPreparationJob: job,
          ...chapterCandidateViewPatch(
            this.data.workspace,
            job,
            this.data.candidateMode,
            this.data.candidatePreviewVersion,
          ),
        });
        return;
      }
      this.setData({
        chapterPreparationJob: null,
        ...chapterCandidateViewPatch(this.data.workspace, null),
      });
      if (!wasActive) {
        return;
      }
      await this.loadPageData(true);
      wx.showToast({
        title: job.operation === "rewrite" ? "小节正文已更新" : "本章处理完成",
        icon: "success",
      });
      return;
    }
    if (job.status === "cancelled" && initial) {
      this.setData({ chapterPreparationJob: null });
      return;
    }
    if (job.status === "failed" || job.status === "cancelled") {
      const view = chapterPreparationView(job);
      this.setData({
        chapterPreparationJob: job,
        chapterPreparationTitle: view.title,
        chapterPreparationDetail: view.detail,
        chapterPreparationProgress: view.progress,
        chapterPreparationProgressMode: view.progressMode,
      });
      return;
    }
    this.setData({ chapterPreparationJob: null });
  },

  async cancelChapterPreparation() {
    const job = this.data.chapterPreparationJob;
    if (!job?.job_id || !job.can_cancel) {
      return;
    }
    this.setData({
      chapterPreparationJob: { ...job, can_cancel: false },
      chapterPreparationTitle: "正在取消处理",
      chapterPreparationDetail: "正在停止后续模型调用，已保存的进度仍会保留",
      chapterPreparationProgressMode: "indeterminate",
    });
    try {
      await cancelMemoirChapterPreparationJob(
        this.data.projectId,
        this.data.chapterId,
        job.job_id,
      );
      this.stopPreparationPolling();
      this.finishOperation(
        job.operation === "rewrite" ? "rewrite_chapter" : "prepare",
      );
      this.setData({ chapterPreparationJob: null });
      wx.showToast({ title: "已取消处理", icon: "none" });
    } catch (error) {
      if (routeAuthError(error, "login")) {
        this.finishOperation(
          job.operation === "rewrite" ? "rewrite_chapter" : "prepare",
        );
        return;
      }
      this.setData({
        chapterPreparationJob: job,
        error: error instanceof Error ? error.message : "取消处理失败",
      });
    }
  },

  async acceptChapterCandidate() {
    const job = this.data.chapterPreparationJob;
    if (!job?.job_id || !job.candidate_content || this.data.operationKind || this.data.editingSectionId) return;
    const projectId = this.data.projectId;
    const chapterId = this.data.chapterId;
    this.setData({ operationKind: "prepare", error: "" });
    try {
      await acceptMemoirChapterCandidate(projectId, chapterId, job.job_id);
      if (!this.pageVisible || this.data.projectId !== projectId || this.data.chapterId !== chapterId) return;
      this.setData({
        chapterPreparationJob: null,
        ...chapterCandidateViewPatch(this.data.workspace, null),
      });
      await this.loadPageData(true);
      wx.showToast({ title: "已采用新稿", icon: "success" });
    } catch (error) {
      if (this.pageVisible && this.data.projectId === projectId && this.data.chapterId === chapterId) {
        this.setData({ error: error instanceof Error ? error.message : "新稿采用失败" });
      }
    } finally {
      this.finishOperation("prepare");
    }
  },

  async refreshSectionRegenerationJob(initial = false) {
    if (!this.data.projectId || !this.data.chapterId) {
      return;
    }
    const loadSequence = ++this.sectionRegenerationLoadSequence;
    try {
      const job = await getMemoirChapterSectionRegenerationJob(
        this.data.projectId,
        this.data.chapterId,
      );
      if (
        !this.pageVisible
        || loadSequence !== this.sectionRegenerationLoadSequence
      ) {
        return;
      }
      await this.applySectionRegenerationJob(job, initial);
    } catch (error) {
      if (routeAuthError(error, "login")) {
        return;
      }
      if (loadSequence !== this.sectionRegenerationLoadSequence) {
        return;
      }
      if (!initial) {
        this.setData({
          error: error instanceof Error ? error.message : "整理进度读取失败",
        });
        this.scheduleSectionRegenerationPolling();
      }
    }
  },

  async applySectionRegenerationJob(
    job: MemoirChapterSectionRegenerationJob,
    initial = false,
  ) {
    const previous = this.data.sectionRegenerationJob;
    const wasActive = (
      previous?.job_id === job.job_id
      && isActiveSectionRegeneration(previous)
    );
    if (isActiveSectionRegeneration(job)) {
      const view = sectionRegenerationView(job);
      this.setData({
        operationKind: "regenerate_sections",
        operationTargetId: "",
        sectionRegenerationJob: job,
        sectionRegenerationTitle: view.title,
        sectionRegenerationDetail: view.detail,
        sectionRegenerationProgress: view.progress,
        sectionRegenerationProgressMode: view.progressMode,
        error: "",
      });
      this.scheduleSectionRegenerationPolling();
      return;
    }

    this.stopSectionRegenerationPolling();
    this.finishOperation("regenerate_sections");
    if (job.status === "succeeded") {
      this.setData({
        sectionRegenerationJob: null,
        expandedSectionIds: {},
      });
      if (initial && !wasActive) {
        return;
      }
      await this.loadPageData(true);
      if (job.unassigned_count > 0) {
        wx.showModal({
          title: "有回忆暂未归类",
          content: `已整理为 ${job.section_count} 小节；另有 ${job.unassigned_count} 段回忆暂不纳入本章正文。您可以稍后归类，也可以进入回忆详情编辑或删除。`,
          showCancel: true,
          confirmText: "查看回忆",
          cancelText: "稍后处理",
          success: (modalResult) => {
            if (modalResult.confirm) {
              this.revealPendingAssignments();
            }
          },
        });
      } else {
        wx.showToast({
          title: `已整理为${job.section_count}小节`,
          icon: "success",
        });
      }
      return;
    }

    if (job.status === "cancelled" && initial) {
      this.setData({ sectionRegenerationJob: null });
      return;
    }
    if (job.status === "failed" || job.status === "cancelled") {
      const view = sectionRegenerationView(job);
      this.setData({
        sectionRegenerationJob: job,
        sectionRegenerationTitle: view.title,
        sectionRegenerationDetail: view.detail,
        sectionRegenerationProgress: view.progress,
        sectionRegenerationProgressMode: view.progressMode,
      });
      return;
    }
    this.setData({ sectionRegenerationJob: null });
  },

  async cancelSectionRegeneration() {
    const job = this.data.sectionRegenerationJob;
    if (!job?.job_id || !job.can_cancel) {
      return;
    }
    this.setData({
      sectionRegenerationJob: { ...job, can_cancel: false },
      sectionRegenerationTitle: "正在取消整理",
      sectionRegenerationDetail: "正在停止后续处理，原小节结构会保持不变",
      sectionRegenerationProgressMode: "indeterminate",
    });
    try {
      await cancelMemoirChapterSectionRegenerationJob(
        this.data.projectId,
        this.data.chapterId,
        job.job_id,
      );
      this.stopSectionRegenerationPolling();
      this.finishOperation("regenerate_sections");
      this.setData({ sectionRegenerationJob: null });
      wx.showToast({ title: "已取消整理", icon: "none" });
    } catch (error) {
      if (routeAuthError(error, "login")) {
        return;
      }
      this.setData({
        sectionRegenerationJob: job,
        error: error instanceof Error ? error.message : "取消整理失败",
      });
    }
  },

  revealPendingAssignments() {
    const pendingSection = this.data.sectionViews.find(
      (section) => section.requires_user_assignment,
    );
    if (pendingSection) {
      const expandedSectionIds = {
        ...this.data.expandedSectionIds,
        [pendingSection.id]: true,
      };
      this.setData({
        expandedSectionIds,
        ...(this.data.workspace
          ? chapterWorkspaceViewPatch(
            this.data.workspace,
            expandedSectionIds,
          )
          : {}),
      });
    }
    scrollToSection("#chapter-sections");
  },

  rewriteChapter() {
    const workspace = this.data.workspace;
    if (
      !workspace?.draft_needs_rewrite
      || !workspace.can_rewrite_draft
      || Boolean(this.data.operationKind)
      || Boolean(this.data.editingSectionId)
    ) {
      return;
    }
    wx.showModal({
      title: this.data.rewriteDraftActionText,
      content: this.data.rewriteDraftScopeDetail,
      confirmText: "更新正文",
      cancelText: "暂不更新",
      success: (result) => {
        if (result.confirm) {
          void this.performChapterRewrite();
        }
      },
    });
  },

  async performChapterRewrite() {
    if (!this.beginOperation("rewrite_chapter")) {
      return;
    }
    try {
      const job = await enqueueMemoirChapterPreparationJob(
        this.data.projectId,
        this.data.chapterId,
        "rewrite",
      );
      await this.applyChapterPreparationJob(job);
    } catch (error) {
      if (routeAuthError(error, "login")) {
        return;
      }
      this.finishOperation("rewrite_chapter");
      this.setData({
        error: error instanceof Error ? error.message : "本章正文更新失败",
      });
    }
  },

  schedulePreparationPolling() {
    if (
      !this.pageVisible
      || this.preparationPoller.pending
      || !isActiveChapterPreparation(this.data.chapterPreparationJob)
    ) {
      return;
    }
    this.preparationPoller.schedule(() => {
      if (this.pageVisible) {
        void this.refreshChapterPreparationJob();
      }
    });
  },

  stopPreparationPolling() {
    this.preparationPoller.cancel();
  },

  scheduleSectionRegenerationPolling() {
    if (
      !this.pageVisible
      || this.sectionRegenerationPoller.pending
      || !isActiveSectionRegeneration(this.data.sectionRegenerationJob)
    ) {
      return;
    }
    this.sectionRegenerationPoller.schedule(() => {
      if (this.pageVisible) {
        void this.refreshSectionRegenerationJob();
      }
    });
  },

  stopSectionRegenerationPolling() {
    this.sectionRegenerationPoller.cancel();
  },
});
