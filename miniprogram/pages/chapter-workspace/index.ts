import type { MemoirChapterWorkspace } from "../../domain/chapter-workspace";
import {
  accessibilityPageClass,
  accessibilityPagePatch,
} from "../../services/accessibility-settings";
import { AudioPlaybackController } from "../../services/audio-playback";
import { definePage } from "../../services/page-definition";
import {
  closeMemoirCompletionIssue,
  listMemoirCompletionIssues,
} from "../../services/memoir-completion-issue-api";
import { enqueueMemoirChapterPreparationJob } from "../../services/memoir-book-completion-api";
import {
  getMemoirChapterWorkspace,
  placeMemoirChapterMaterial,
  type MemoirSectionMaterialPlacementRequest,
} from "../../services/memoir-chapter-workspace-api";
import { redirectToBook, routeAuthError } from "../../services/navigation";
import {
  pageUrl,
  datasetString,
  pickerIndex,
  queryParam,
  type PageDatasetEvent,
  type PagePickerEvent,
  type PageQuery,
} from "../../services/page-input";
import { setPageNavigationTitle } from "../../services/page-navigation";
import { LOADING_NEXT_ACTION } from "./chapter-next-action";
import { chapterWorkspaceViewPatch } from "./chapter-workspace-view";
import {
  readCachedMemoirChapterWorkspace,
  writeCachedMemoirChapterWorkspace,
} from "../../stores/memoir-read-cache";
import { promptRequiredText } from "../../services/text-prompt";
import {
  CLOSED_DRAFT_SECTION_EDITOR,
  PREPARATION_POLL_INTERVAL_MS,
  SECTION_REGENERATION_POLL_INTERVAL_MS,
  isActiveChapterPreparation,
  readableIssue,
  readableReviewText,
  scrollToSection,
  type ChapterWorkspaceOperationKind,
  type ChapterWorkspacePageData,
} from "./chapter-workspace-model";
import {
  chapterWorkspaceDraftMethods,
} from "./chapter-workspace-draft-workflow";
import {
  chapterWorkspaceJobMethods,
} from "./chapter-workspace-job-workflow";
import { PollingTimer } from "../../services/polling-timer";

export { chapterWorkspaceViewPatch };

const PAGE_CLASS = "workspace-page ui-page-shell ui-accessible-page";

definePage<ChapterWorkspacePageData>()({
  audioPlayback: new AudioPlaybackController(),
  playToken: 0,
  preparationPoller: new PollingTimer(PREPARATION_POLL_INTERVAL_MS),
  sectionRegenerationPoller: new PollingTimer(SECTION_REGENERATION_POLL_INTERVAL_MS),
  pageVisible: false,
  loadedOnce: false,
  workspaceLoadSequence: 0,
  chapterPreparationLoadSequence: 0,
  sectionRegenerationLoadSequence: 0,

  data: {
    pageClass: accessibilityPageClass(PAGE_CLASS),
    loading: true,
    error: "",
    projectId: "",
    chapterId: "",
    workspace: null,
    nextAction: LOADING_NEXT_ACTION,
    readinessTitle: "正在读取本章",
    readinessDetail: "",
    rewriteNoticeTitle: "",
    rewriteNoticeDetail: "",
    draftRecoveryTitle: "",
    draftRecoveryDetail: "",
    draftRecoveryActionText: "",
    draftRecoveryActionKind: "none",
    draftRecoveryActionDisabled: true,
    narrativeSectionCount: 0,
    sectionStructureSummary: "",
    rewriteDraftActionText: "更新有变化的小节正文",
    rewriteDraftBusyText: "正在更新小节正文…",
    rewriteDraftScopeDetail: "",
    suggestionsExpanded: false,
    requiredIssues: [],
    optionalIssues: [],
    sectionViews: [],
    retainedDraftSectionCount: 0,
    expandedSectionIds: {},
    canConfirmChapter: false,
    operationKind: "",
    operationTargetId: "",
    chapterPreparationJob: null,
    chapterPreparationTitle: "",
    chapterPreparationDetail: "",
    chapterPreparationProgress: 0,
    chapterPreparationProgressMode: "hidden",
    sectionRegenerationJob: null,
    sectionRegenerationTitle: "",
    sectionRegenerationDetail: "",
    sectionRegenerationProgress: 0,
    sectionRegenerationProgressMode: "hidden",
    audioPhase: "idle",
    audioTarget: "",
    ...CLOSED_DRAFT_SECTION_EDITOR,
  },

  async onLoad(query: PageQuery = {}) {
    const projectId = queryParam(query, "projectId");
    const chapterId = queryParam(query, "chapterId");
    this.setData({
      projectId,
      chapterId,
    });
    if (!projectId || !chapterId) {
      this.setData({ loading: false, error: "缺少章节信息" });
      return;
    }
    this.pageVisible = true;
    await Promise.all([
      this.loadPageData(),
      this.refreshChapterPreparationJob(true),
      this.refreshSectionRegenerationJob(true),
    ]);
    this.loadedOnce = true;
  },

  async onShow() {
    this.setData(accessibilityPagePatch(PAGE_CLASS));
    this.pageVisible = true;
    if (this.loadedOnce) {
      await Promise.all([
        this.loadPageData(true),
        this.refreshChapterPreparationJob(true),
        this.refreshSectionRegenerationJob(true),
      ]);
    }
  },

  onHide() {
    this.pageVisible = false;
    this.stopPreparationPolling();
    this.stopSectionRegenerationPolling();
    this.stopDraftSectionPlayback();
  },

  onUnload() {
    this.pageVisible = false;
    this.stopPreparationPolling();
    this.stopSectionRegenerationPolling();
    this.stopDraftSectionPlayback();
  },

  async onPullDownRefresh() {
    await Promise.all([
      this.loadPageData(true),
      this.refreshChapterPreparationJob(),
      this.refreshSectionRegenerationJob(),
    ]);
    wx.stopPullDownRefresh();
  },

  beginOperation(
    kind: ChapterWorkspaceOperationKind,
    targetId = "",
  ): boolean {
    const editingAllowsOperation = (
      kind === "save_draft_section"
      && this.data.editingMode === "manual"
    );
    if (
      !kind
      || this.data.operationKind
      || (
        this.data.editingSectionId
        && !editingAllowsOperation
      )
    ) {
      return false;
    }
    this.stopDraftSectionPlayback();
    this.setData({
      operationKind: kind,
      operationTargetId: targetId,
      error: "",
    });
    return true;
  },

  finishOperation(
    kind: ChapterWorkspaceOperationKind,
    targetId = "",
  ) {
    if (
      this.data.operationKind !== kind
      || (targetId && this.data.operationTargetId !== targetId)
    ) {
      return;
    }
    this.setData({
      operationKind: "",
      operationTargetId: "",
    });
  },

  hasUnsavedSectionChanges() {
    if (!this.data.editingSectionId || !this.data.workspace) {
      return false;
    }
    const section = this.data.workspace.draft_sections.find(
      (item) => item.section_id === this.data.editingSectionId,
    );
    return Boolean(
      section
      && section.content !== this.data.editingSectionContent,
    );
  },

  leaveAfterEditConfirmation(navigate: () => void) {
    if (!this.hasUnsavedSectionChanges()) {
      navigate();
      return;
    }
    wx.showModal({
      title: "放弃当前修改？",
      content: "当前小节的修改还没有完成，离开后这些内容会丢失。",
      confirmText: "放弃修改",
      cancelText: "继续编辑",
      success: (result) => {
        if (result.confirm) {
          navigate();
        }
      },
    });
  },

  openBookHome() {
    this.leaveAfterEditConfirmation(() => {
      redirectToBook();
    });
  },

  returnToBookFlow() {
    this.leaveAfterEditConfirmation(() => {
      wx.navigateBack({
        fail: () => {
          redirectToBook();
        },
      });
    });
  },

  async loadPageData(silent = false) {
    if (!this.data.projectId || !this.data.chapterId) {
      return;
    }
    const loadSequence = ++this.workspaceLoadSequence;
    const cached = readCachedMemoirChapterWorkspace(
      this.data.projectId,
      this.data.chapterId,
    );
    const showedCachedData = Boolean(this.data.workspace || cached.payload);
    const localInvalidationRevision = cached.localInvalidationRevision || 0;
    if (!this.data.workspace && cached.payload) {
      this.applyWorkspace(cached.payload);
    } else if (!silent) {
      this.setData({ loading: true, error: "" });
    }
    try {
      const [workspace, issueCollection] = await Promise.all([
        getMemoirChapterWorkspace(
          this.data.projectId,
          this.data.chapterId,
        ),
        listMemoirCompletionIssues(this.data.projectId, {
          chapterId: this.data.chapterId,
        }),
      ]);
      if (loadSequence !== this.workspaceLoadSequence) {
        return;
      }
      const currentCache = readCachedMemoirChapterWorkspace(
        this.data.projectId,
        this.data.chapterId,
      );
      if (
        currentCache.status !== "miss"
        && (currentCache.localInvalidationRevision || 0) !== localInvalidationRevision
      ) {
        return;
      }
      writeCachedMemoirChapterWorkspace(workspace);
      this.applyWorkspace(workspace);
      this.setData({
        requiredIssues: issueCollection.issues.filter(
          (issue) => issue.channel === "manual_review",
        ).map((issue) => readableIssue(issue, workspace)),
        optionalIssues: issueCollection.issues.filter(
          (issue) => issue.channel === "interview",
        ).map((issue) => readableIssue(issue, workspace)),
      });
    } catch (error) {
      if (loadSequence !== this.workspaceLoadSequence) {
        return;
      }
      if (routeAuthError(error, "login")) {
        return;
      }
      if (showedCachedData) {
        console.warn("refresh memoir chapter workspace failed", error);
      } else {
        this.setData({
          loading: false,
          error: error instanceof Error ? error.message : "本章内容加载失败",
        });
      }
    }
  },

  applyWorkspace(workspace: MemoirChapterWorkspace) {
    const summary = workspace.completion_summary;
    const viewWorkspace = {
      ...workspace,
      completion_summary: {
        ...summary,
        detail: readableReviewText(summary.detail, workspace),
        next_action: {
          ...summary.next_action,
          detail: readableReviewText(summary.next_action.detail, workspace),
        },
        active_issue: summary.active_issue
          ? {
            ...summary.active_issue,
            question: readableReviewText(
              summary.active_issue.question,
              workspace,
            ),
          }
          : summary.active_issue,
      },
    };
    setPageNavigationTitle(viewWorkspace.chapter_title || "本章正文");
    this.setData({
      workspace: viewWorkspace,
      ...chapterWorkspaceViewPatch(
        viewWorkspace,
        this.data.expandedSectionIds,
      ),
      loading: false,
      error: "",
    });
    if (!isActiveChapterPreparation(this.data.chapterPreparationJob)) {
      this.stopPreparationPolling();
    }
  },

  ...chapterWorkspaceJobMethods,

  openMaterialEditor(
    event: PageDatasetEvent<{ utteranceId?: string }>,
  ) {
    const utteranceId = datasetString(event, "utteranceId");
    if (!utteranceId) {
      return;
    }
    wx.navigateTo({
      url: pageUrl("/pages/transcript-detail/index", { id: utteranceId }),
    });
  },

  openChapterPreview() {
    if (
      !this.data.workspace?.draft_version_id
      || !this.data.workspace.draft_content.trim()
      || Boolean(this.data.operationKind)
      || Boolean(this.data.editingSectionId)
    ) {
      return;
    }
    this.stopDraftSectionPlayback();
    wx.navigateTo({
      url: pageUrl("/pages/book-reader/index", {
        projectId: this.data.projectId,
        chapterId: this.data.chapterId,
      }),
    });
  },

  async runNextAction() {
    const action = this.data.nextAction;
    if (
      action.disabled
      || Boolean(this.data.operationKind)
      || Boolean(this.data.editingSectionId)
    ) {
      return;
    }
    if (action.kind === "prepare") {
      await this.prepareChapter();
      return;
    }
    if (action.kind === "confirm") {
      this.confirmCurrentIssue();
      return;
    }
    if (action.kind === "review_sections") {
      this.revealPendingAssignments();
      return;
    }
    if (action.kind === "review_draft" || action.kind === "complete") {
      scrollToSection("#chapter-draft");
    }
  },

  moveUnassignedMaterial(
    event: PagePickerEvent<{ utteranceId?: string }>,
  ) {
    const utteranceId = datasetString(event, "utteranceId");
    const targetIndex = pickerIndex(event);
    const material = this.data.sectionViews
      .flatMap((section) => section.visibleMaterials)
      .find((candidate) => candidate.utterance_id === utteranceId);
    const target = material?.assignmentOptions[targetIndex];
    if (
      !utteranceId
      || !target
      || Boolean(this.data.operationKind)
      || Boolean(this.data.editingSectionId)
    ) {
      return;
    }
    if (target.action === "create_section") {
      void this.createSectionForMaterial(utteranceId);
      return;
    }
    wx.showModal({
      title: "确认素材归属",
      content: `把这条讲述移到“${target.title}”？`,
      confirmText: "确认移动",
      cancelText: "再想想",
      success: (result) => {
        if (result.confirm) {
          void this.performMaterialPlacement(
            utteranceId,
            { target_section_id: target.id },
            "已选择小节",
          );
        }
      },
    });
  },

  async createSectionForMaterial(utteranceId: string) {
    const title = await promptRequiredText("新建小节", "请输入小节名称");
    if (!title) {
      return;
    }
    await this.performMaterialPlacement(
      utteranceId,
      {
        target_chapter_id: this.data.chapterId,
        new_section_title: title,
      },
      "已新建小节",
    );
  },

  async performMaterialPlacement(
    utteranceId: string,
    placement: MemoirSectionMaterialPlacementRequest,
    successTitle: string,
  ) {
    if (
      !utteranceId
      || !this.beginOperation("move_material", utteranceId)
    ) {
      return;
    }
    try {
      await placeMemoirChapterMaterial(
        this.data.projectId,
        utteranceId,
        placement,
      );
      await this.loadPageData(true);
      wx.showToast({ title: successTitle, icon: "success" });
    } catch (error) {
      if (routeAuthError(error, "login")) {
        return;
      }
      this.setData({
        error: error instanceof Error
          ? error.message
          : "素材位置更新失败",
      });
    } finally {
      this.finishOperation("move_material", utteranceId);
    }
  },

  async runDraftRecoveryAction() {
    const workspace = this.data.workspace;
    if (
      !workspace?.draft_content
      || workspace.draft_sections.length > 0
      || this.data.draftRecoveryActionDisabled
      || Boolean(this.data.operationKind)
      || Boolean(this.data.editingSectionId)
    ) {
      return;
    }
    if (this.data.draftRecoveryActionKind === "rewrite") {
      this.rewriteChapter();
      return;
    }
    if (this.data.draftRecoveryActionKind === "next") {
      await this.runNextAction();
      return;
    }
    if (this.data.draftRecoveryActionKind === "status") {
      scrollToSection("#chapter-status");
    }
  },

  async prepareChapter() {
    if (!this.beginOperation("prepare")) {
      return;
    }
    this.stopPreparationPolling();
    try {
      const job = await enqueueMemoirChapterPreparationJob(
        this.data.projectId,
        this.data.chapterId,
        "prepare",
      );
      await this.applyChapterPreparationJob(job);
    } catch (error) {
      if (routeAuthError(error, "login")) {
        return;
      }
      this.finishOperation("prepare");
      this.setData({ error: error instanceof Error ? error.message : "本章整理失败" });
    }
  },

  confirmCurrentIssue() {
    const issue = this.data.workspace?.completion_summary.active_issue;
    if (
      !issue
      || !this.data.nextAction.issueId
      || this.data.operationKind
    ) {
      return;
    }
    wx.showModal({
      title: "请确认这一点",
      content: issue.question,
      confirmText: "确认",
      cancelText: "先不处理",
      success: (result) => {
        if (result.confirm) {
          void this.applyCurrentIssueConfirmation(issue.id);
        }
      },
    });
  },

  async applyCurrentIssueConfirmation(
    issueId: string,
    note = "用户已确认按当前建议处理",
  ) {
    if (
      !issueId
      || !this.beginOperation("confirm_issue", issueId)
    ) {
      return;
    }
    try {
      await closeMemoirCompletionIssue(this.data.projectId, issueId, {
        outcome: "confirmed",
        note,
      });
      const job = await enqueueMemoirChapterPreparationJob(
        this.data.projectId,
        this.data.chapterId,
        "prepare",
      );
      await this.applyChapterPreparationJob(job);
    } catch (error) {
      if (routeAuthError(error, "login")) {
        return;
      }
      this.finishOperation("confirm_issue", issueId);
      this.setData({ error: error instanceof Error ? error.message : "确认失败" });
    }
  },

  ...chapterWorkspaceDraftMethods,


});
