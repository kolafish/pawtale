import type { MemoirChapterWorkspace } from "../../domain/chapter-workspace";
import {
  accessibilityPageClass,
  accessibilityPagePatch,
} from "../../services/accessibility-settings";
import { AudioPlaybackController } from "../../services/audio-playback";
import { definePage, pageRuntime } from "../../services/page-definition";
import {
  closeMemoirCompletionIssue,
  listMemoirCompletionIssues,
} from "../../services/memoir-completion-issue-api";
import { enqueueMemoirChapterPreparationJob } from "../../services/memoir-book-completion-api";
import {
  getMemoirChapterWorkspace,
  placeMemoirChapterMaterial,
  renameMemoirChapterSection,
  type MemoirSectionMaterialPlacementRequest,
} from "../../services/memoir-chapter-workspace-api";
import {
  redirectToBook,
  routeAuthError,
} from "../../services/navigation";
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
import {
  chapterCandidateViewPatch,
  chapterWorkspaceViewPatch,
} from "./chapter-workspace-view";
import {
  readCachedMemoirChapterWorkspace,
  writeCachedMemoirChapterWorkspace,
} from "../../stores/memoir-read-cache";
import { promptRequiredText } from "../../services/text-prompt";
import { trackAuditAction } from "../../services/audit-tracker";
import {
  applyMemoirBookPlanStructureCommand,
  generateMemoirBookChapterTitle,
  getMemoirBookPlan,
  updateMemoirBookChapterTitle,
} from "../../services/memoir-book-api";
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
  audioPlayback: pageRuntime(() => new AudioPlaybackController()),
  playToken: 0,
  preparationPoller: pageRuntime(
    () => new PollingTimer(PREPARATION_POLL_INTERVAL_MS),
  ),
  sectionRegenerationPoller: pageRuntime(
    () => new PollingTimer(SECTION_REGENERATION_POLL_INTERVAL_MS),
  ),
  pageVisible: false,
  loadedOnce: false,
  workspaceLoadSequence: 0,
  chapterPreparationLoadSequence: 0,
  sectionRegenerationLoadSequence: 0,
  sectionFocusId: "",

  data: {
    pageClass: accessibilityPageClass(PAGE_CLASS),
    loading: true,
    error: "",
    projectId: "",
    chapterId: "",
    focusedSectionId: "",
    focusedSectionTitle: "",
    focusedSection: null,
    focusedStoryExpanded: false,
    workspace: null,
    nextAction: LOADING_NEXT_ACTION,
    chapterHeroKicker: "这一章正在慢慢长大",
    chapterStoryCountText: "故事正在生长",
    chapterStoryDetail: "讲过的回忆都在这里，整理好后就可以慢慢读了。",
    chapterHasReadableDraft: false,
    chapterEditingHint: "",
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
    rewriteDraftActionText: "更新有变化的故事",
    rewriteDraftBusyText: "正在更新故事…",
    rewriteDraftScopeDetail: "",
    editingToolsExpanded: false,
    requiredIssues: [],
    optionalIssues: [],
    sectionViews: [],
    retainedDraftSectionCount: 0,
    expandedSectionIds: {},
    operationKind: "",
    operationTargetId: "",
    chapterPreparationJob: null,
    chapterPreparationTitle: "",
    chapterPreparationDetail: "",
    chapterPreparationProgress: 0,
    chapterPreparationProgressMode: "hidden",
    candidateMode: "changes",
    candidatePreviewVersion: "after",
    candidateComparisonAvailable: false,
    candidateSummaryText: "",
    candidateDiffSegments: [],
    candidateWholeContent: "",
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
    this.sectionFocusId = queryParam(query, "sectionId");
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
    wx.disableAlertBeforeUnload?.();
    this.draftSectionUnloadAlertEnabled = false;
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

  draftSectionUnloadAlertEnabled: false,

  syncDraftSectionUnloadAlert() {
    if (typeof wx === "undefined" || typeof wx.enableAlertBeforeUnload !== "function") return;
    const enabled = this.hasUnsavedSectionChanges();
    if (enabled === this.draftSectionUnloadAlertEnabled) return;
    this.draftSectionUnloadAlertEnabled = enabled;
    if (enabled) {
      wx.enableAlertBeforeUnload({ message: "尚未保存的故事正文会丢失，确定离开吗？" });
    } else {
      wx.disableAlertBeforeUnload?.();
    }
  },

  leaveAfterEditConfirmation(navigate: () => void) {
    if (!this.hasUnsavedSectionChanges()) {
      navigate();
      return;
    }
    wx.showModal({
      title: "放弃当前修改？",
      content: "当前故事的修改还没有完成，离开后这些内容会丢失。",
      confirmText: "放弃修改",
      cancelText: "继续编辑",
      success: (result) => {
        if (result.confirm) {
          wx.disableAlertBeforeUnload?.();
          this.draftSectionUnloadAlertEnabled = false;
          navigate();
        }
      },
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
        requiredIssues: this.data.focusedSectionId
          ? []
          : issueCollection.issues.filter(
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
    const focusedSectionId = this.sectionFocusId;
    const hasFocusedSection = Boolean(
      focusedSectionId
      && viewWorkspace.sections.some((section) => section.id === focusedSectionId),
    );
    const expandedSectionIds = hasFocusedSection
      ? {
        ...this.data.expandedSectionIds,
        [focusedSectionId]: true,
      }
      : this.data.expandedSectionIds;
    const viewPatch = chapterWorkspaceViewPatch(
      viewWorkspace,
      expandedSectionIds,
    );
    const focusedSection = hasFocusedSection
      ? viewPatch.sectionViews.find((section) => section.id === focusedSectionId)
      : undefined;
    setPageNavigationTitle(
      focusedSection?.title || viewWorkspace.chapter_title || "本章正文",
    );
    this.setData({
      workspace: viewWorkspace,
      ...viewPatch,
      ...chapterCandidateViewPatch(
        viewWorkspace,
        this.data.chapterPreparationJob,
        this.data.candidateMode,
        this.data.candidatePreviewVersion,
      ),
      focusedSectionId: focusedSection?.id || "",
      focusedSectionTitle: focusedSection?.title || "",
      focusedSection: focusedSection || null,
      sectionViews: focusedSection
        ? [{ ...focusedSection, expanded: true }]
        : viewPatch.sectionViews,
      sectionStructureSummary: focusedSection?.metaText || viewPatch.sectionStructureSummary,
      expandedSectionIds,
      loading: false,
      error: "",
    });
    this.syncDraftSectionUnloadAlert();
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

  async renameSection(
    event: PageDatasetEvent<{ id?: string; title?: string; version?: string }>,
  ) {
    const sectionId = datasetString(event, "id");
    const currentTitle = datasetString(event, "title");
    const expectedVersion = Number(datasetString(event, "version"));
    if (!sectionId || !expectedVersion || this.data.operationKind) {
      return;
    }
    const title = await promptRequiredText("编辑故事标题", currentTitle || "请输入故事标题");
    if (!title || title === currentTitle || !this.beginOperation("rename_section", sectionId)) {
      return;
    }
    try {
      await renameMemoirChapterSection(
        this.data.projectId,
        sectionId,
        title,
        expectedVersion,
      );
      await this.loadPageData(true);
      wx.showToast({ title: "故事标题已更新", icon: "success" });
    } catch (error) {
      if (routeAuthError(error, "login")) {
        return;
      }
      this.setData({
        error: error instanceof Error ? error.message : "故事标题更新失败",
      });
    } finally {
      this.finishOperation("rename_section", sectionId);
    }
  },

  openFocusedSectionMedia() {
    if (
      !this.data.projectId
      || !this.data.chapterId
      || !this.data.focusedSectionId
    ) {
      return;
    }
    wx.navigateTo({
      url: pageUrl("/pages/image-assets/index", {
        projectId: this.data.projectId,
        scope: "section",
        chapterId: this.data.chapterId,
        sectionId: this.data.focusedSectionId,
      }),
    });
  },

  toggleFocusedStory() {
    if (!this.data.focusedSection?.draftCanExpand) {
      return;
    }
    this.setData({ focusedStoryExpanded: !this.data.focusedStoryExpanded });
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

  async openChapterMoreActions() {
    if (!this.data.workspace || this.data.operationKind || this.data.editingSectionId) {
      return;
    }
    this.setData({ operationKind: "chapter_settings", operationTargetId: "menu", error: "" });
    try {
      const plan = await getMemoirBookPlan(this.data.projectId);
      if (!this.pageVisible) {
        return;
      }
      const chapter = plan.chapters.find((item) => item.id === this.data.chapterId);
      if (!chapter) {
        throw new Error("没有找到这一章，请返回书稿刷新。");
      }
      const actions = [
        { label: "修改章名", run: () => this.editChapterTitle(false) },
        { label: "AI 重新起名", run: () => this.editChapterTitle(true) },
      ];
      if (this.data.chapterHasReadableDraft) {
        actions.push({
          label: this.data.audioTarget === "chapter" && this.data.audioPhase !== "idle"
            ? "停止朗读" : "朗读整章",
          run: () => this.playChapterAudio(),
        });
      }
      const includeInBook = chapter.include_in_book === false;
      actions.push({
        label: includeInBook ? "恢复收入本书" : "不收入本书",
        run: () => this.setChapterBookInclusion(includeInBook),
      });
      this.finishOperation("chapter_settings", "menu");
      wx.showActionSheet({
        itemList: actions.map((action) => action.label),
        success: (result) => { void actions[result.tapIndex]?.run(); },
      });
    } catch (error) {
      if (!routeAuthError(error, "login")) {
        this.setData({ error: error instanceof Error ? error.message : "本章操作加载失败" });
      }
    } finally {
      this.finishOperation("chapter_settings", "menu");
    }
  },

  async editChapterTitle(useAI: boolean) {
    if (!this.data.workspace || !this.beginOperation("chapter_settings")) {
      return;
    }
    try {
      if (useAI) {
        await generateMemoirBookChapterTitle(this.data.projectId, this.data.chapterId, {});
      } else {
        const title = await promptRequiredText("修改章名", this.data.workspace.chapter_title);
        if (!title || title === this.data.workspace.chapter_title) {
          return;
        }
        if (title.length > 80) {
          wx.showToast({ title: "章名不能超过 80 字", icon: "none" });
          return;
        }
        await updateMemoirBookChapterTitle(this.data.projectId, this.data.chapterId, { title });
      }
      await this.loadPageData(true);
      trackAuditAction(
        useAI ? "book.chapter_title_generate" : "book.chapter_title_update",
        {},
        { projectId: this.data.projectId },
      );
      wx.showToast({ title: useAI ? "已重新起名" : "章名已修改", icon: "success" });
    } catch (error) {
      if (!routeAuthError(error, "login")) {
        this.setData({ error: error instanceof Error ? error.message : "章名更新失败" });
      }
    } finally {
      this.finishOperation("chapter_settings");
    }
  },

  async setChapterBookInclusion(includeInBook: boolean) {
    if (!this.data.workspace || !this.beginOperation("chapter_settings")) {
      return;
    }
    try {
      await applyMemoirBookPlanStructureCommand(this.data.projectId, {
        type: "set_chapter_book_inclusion",
        chapter_id: this.data.chapterId,
        include_in_book: includeInBook,
      });
      await this.loadPageData(true);
      wx.showToast({ title: includeInBook ? "已恢复收入本书" : "已设为不收入本书", icon: "success" });
    } catch (error) {
      if (!routeAuthError(error, "login")) {
        this.setData({ error: error instanceof Error ? error.message : "收录设置更新失败" });
      }
    } finally {
      this.finishOperation("chapter_settings");
    }
  },

  async playChapterAudio() {
    if (!this.data.chapterHasReadableDraft) {
      return;
    }
    await this.playDraftAudio(
      "chapter",
      { kind: "chapter", chapterId: this.data.chapterId, versionId: this.data.workspace?.draft_version_id || undefined },
      "章节朗读失败",
    );
  },

  showDraftUpdate() {
    if (
      !this.data.workspace?.draft_needs_rewrite
      || Boolean(this.data.operationKind)
      || Boolean(this.data.editingSectionId)
    ) {
      return;
    }
    this.setData({ editingToolsExpanded: true }, () => {
      scrollToSection("#chapter-sections");
    });
  },

  openStorySection(
    event: PageDatasetEvent<{ id?: string }>,
  ) {
    const sectionId = datasetString(event, "id");
    if (
      !sectionId
      || !this.data.projectId
      || !this.data.chapterId
      || Boolean(this.data.operationKind)
      || Boolean(this.data.editingSectionId)
    ) {
      return;
    }
    wx.navigateTo({
      url: pageUrl("/pages/chapter-workspace/index", {
        projectId: this.data.projectId,
        chapterId: this.data.chapterId,
        sectionId,
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
      this.setData({ editingToolsExpanded: true }, () => {
        scrollToSection("#chapter-sections");
      });
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
      title: "确认回忆归属",
      content: `把这段回忆移到“${target.title}”？`,
      confirmText: "确认移动",
      cancelText: "再想想",
      success: (result) => {
        if (result.confirm) {
          void this.performMaterialPlacement(
            utteranceId,
            { target_section_id: target.id },
            "已选择故事",
          );
        }
      },
    });
  },

  async createSectionForMaterial(utteranceId: string) {
    const title = await promptRequiredText("新建故事", "请输入故事标题");
    if (!title) {
      return;
    }
    await this.performMaterialPlacement(
      utteranceId,
      {
        target_chapter_id: this.data.chapterId,
        new_section_title: title,
      },
      "已新建故事",
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
          : "回忆位置更新失败",
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
