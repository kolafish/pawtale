import type { MemoirChapterWorkspace } from "../../domain/chapter-workspace";
import { trackAuditAction } from "../../services/audit-tracker";
import { AudioPlaybackController } from "../../services/audio-playback";
import { definePage } from "../../services/page-definition";
import {
  closeMemoirCompletionIssue,
  confirmMemoirBookChapterDraft,
  createMemoirBookChapterDraftSectionTTS,
  createMemoirVoiceSession,
  getMemoirChapterWorkspace,
  moveMemoirChapterMaterial,
  prepareMemoirBookChapter,
  regenerateMemoirChapterDraftSection,
  regenerateMemoirChapterSections,
  rewriteMemoirBookChapter,
  reviseMemoirBookChapterDraft,
} from "../../services/memoir-voice-api";
import { routeAuthError } from "../../services/navigation";
import {
  pageUrl,
  datasetString,
  pickerIndex,
  queryParam,
  type PageDatasetEvent,
  type PagePickerEvent,
  type PageQuery,
  type PageTextInputEvent,
} from "../../services/page-input";
import { setPageNavigationTitle } from "../../services/page-navigation";
import { readBookTTSSettings } from "../../services/tts-settings";
import {
  readVoiceAuthorizationConsent,
  syncVoiceAuthorizationConsent,
  VOICE_AUTHORIZATION_PAGE_URL,
} from "../../services/voice-authorization";
import {
  LOADING_NEXT_ACTION,
  type ChapterNextAction,
} from "./chapter-next-action";
import {
  chapterWorkspaceViewPatch,
  type ChapterDraftSectionView,
  type ChapterSectionView,
} from "./chapter-workspace-view";

export { chapterWorkspaceViewPatch };

const PREPARATION_POLL_INTERVAL_MS = 1500;

type WxWithPageScrollTo = typeof wx & {
  pageScrollTo?: (options: { selector: string; duration?: number }) => void;
};

function scrollToSection(selector: string) {
  const pageScrollTo = (wx as WxWithPageScrollTo).pageScrollTo;
  if (typeof pageScrollTo === "function") {
    pageScrollTo({ selector, duration: 250 });
  }
}

interface ChapterWorkspacePageData {
  loading: boolean;
  error: string;
  projectId: string;
  chapterId: string;
  workspace: MemoirChapterWorkspace | null;
  nextAction: ChapterNextAction;
  readinessTitle: string;
  readinessDetail: string;
  rewriteNoticeTitle: string;
  rewriteNoticeDetail: string;
  draftRecoveryTitle: string;
  draftRecoveryDetail: string;
  draftRecoveryActionText: string;
  draftRecoveryActionKind: "none" | "next" | "rewrite" | "status";
  draftRecoveryActionDisabled: boolean;
  narrativeSectionCount: number;
  sectionStructureSummary: string;
  rewriteDraftActionText: string;
  rewriteDraftBusyText: string;
  rewriteDraftScopeDetail: string;
  sectionViews: ChapterSectionView[];
  draftSectionViews: ChapterDraftSectionView[];
  expandedSectionIds: Record<string, boolean>;
  expandedDraftSectionIds: Record<string, boolean>;
  canConfirmChapter: boolean;
  processBusy: boolean;
  regeneratingSections: boolean;
  rewritingDraft: boolean;
  startingIssueId: string;
  confirmingChapter: boolean;
  savingDraft: boolean;
  movingMaterialId: string;
  regeneratingDraftSectionId: string;
  ttsBusySectionId: string;
  playingSectionId: string;
  editingSectionId: string;
  editingSectionContent: string;
}

definePage<ChapterWorkspacePageData>()({
  audioPlayback: new AudioPlaybackController(),
  playToken: 0,
  preparationPollTimer: null as ReturnType<typeof setTimeout> | null,
  pageVisible: false,
  loadedOnce: false,

  data: {
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
    rewriteDraftActionText: "重新生成全部小节正文",
    rewriteDraftBusyText: "正在生成全部小节正文…",
    rewriteDraftScopeDetail: "",
    sectionViews: [],
    draftSectionViews: [],
    expandedSectionIds: {},
    expandedDraftSectionIds: {},
    canConfirmChapter: false,
    processBusy: false,
    regeneratingSections: false,
    rewritingDraft: false,
    startingIssueId: "",
    confirmingChapter: false,
    savingDraft: false,
    movingMaterialId: "",
    regeneratingDraftSectionId: "",
    ttsBusySectionId: "",
    playingSectionId: "",
    editingSectionId: "",
    editingSectionContent: "",
  },

  async onLoad(query: PageQuery = {}) {
    const projectId = queryParam(query, "projectId");
    const chapterId = queryParam(query, "chapterId");
    this.setData({ projectId, chapterId });
    if (!projectId || !chapterId) {
      this.setData({ loading: false, error: "缺少章节信息" });
      return;
    }
    this.pageVisible = true;
    await this.loadPageData();
    this.loadedOnce = true;
  },

  async onShow() {
    this.pageVisible = true;
    if (this.loadedOnce) {
      await this.loadPageData(true);
    }
  },

  onHide() {
    this.pageVisible = false;
    this.stopPreparationPolling();
    this.stopDraftSectionPlayback();
  },

  onUnload() {
    this.pageVisible = false;
    this.stopPreparationPolling();
    this.stopDraftSectionPlayback();
  },

  async onPullDownRefresh() {
    await this.loadPageData(true);
    wx.stopPullDownRefresh();
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
      title: "放弃未保存的修改？",
      content: "当前小节的文字还没有保存，离开后这些修改会丢失。",
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
      wx.switchTab({ url: "/pages/book/index" });
    });
  },

  returnToBookFlow() {
    this.leaveAfterEditConfirmation(() => {
      wx.navigateBack({
        fail: () => {
          wx.switchTab({ url: "/pages/book/index" });
        },
      });
    });
  },

  async loadPageData(silent = false) {
    if (!this.data.projectId || !this.data.chapterId) {
      return;
    }
    if (!silent) {
      this.setData({ loading: true, error: "" });
    }
    try {
      const workspace = await getMemoirChapterWorkspace(
        this.data.projectId,
        this.data.chapterId,
      );
      setPageNavigationTitle(workspace.chapter_title || "本章正文");
      this.setData({
        workspace,
        ...chapterWorkspaceViewPatch(
          workspace,
          this.data.expandedSectionIds,
          this.data.expandedDraftSectionIds,
        ),
        loading: false,
        error: "",
      });
      if (workspace.completion_summary.state === "preparing") {
        this.schedulePreparationPolling();
      } else {
        this.stopPreparationPolling();
      }
    } catch (error) {
      if (routeAuthError(error, "login")) {
        return;
      }
      this.setData({
        loading: false,
        error: error instanceof Error ? error.message : "本章内容加载失败",
      });
    }
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
        this.data.expandedDraftSectionIds,
      ),
    });
  },

  toggleDraftSection(event: PageDatasetEvent<{ id?: string }>) {
    const sectionId = datasetString(event, "id");
    const workspace = this.data.workspace;
    if (!sectionId || !workspace || this.data.editingSectionId === sectionId) {
      return;
    }
    const expanded = this.data.draftSectionViews.find(
      (section) => section.section_id === sectionId,
    )?.expanded || false;
    const expandedDraftSectionIds = {
      ...this.data.expandedDraftSectionIds,
      [sectionId]: !expanded,
    };
    this.setData({
      expandedDraftSectionIds,
      ...chapterWorkspaceViewPatch(
        workspace,
        this.data.expandedSectionIds,
        expandedDraftSectionIds,
      ),
    });
  },

  regenerateSections() {
    const workspace = this.data.workspace;
    if (
      !workspace?.sections.length
      || this.data.regeneratingSections
      || Boolean(this.data.regeneratingDraftSectionId)
      || this.data.processBusy
      || this.data.rewritingDraft
      || this.data.confirmingChapter
      || this.data.savingDraft
      || Boolean(this.data.movingMaterialId)
      || Boolean(this.data.editingSectionId)
    ) {
      return;
    }
    wx.showModal({
      title: "重新整理小节分组",
      content: "AI 会重新划分本章讲述的分组和顺序。原始讲述、是否入书的选择和现有正文版本都会保留，完成后需要按新结构重新生成正文。",
      confirmText: "开始整理",
      cancelText: "暂不整理",
      success: (result) => {
        if (result.confirm) {
          void this.performSectionRegeneration();
        }
      },
    });
  },

  async performSectionRegeneration() {
    const workspace = this.data.workspace;
    if (!workspace?.sections.length || this.data.regeneratingSections) {
      return;
    }
    this.stopDraftSectionPlayback();
    this.setData({ regeneratingSections: true, error: "" });
    try {
      const result = await regenerateMemoirChapterSections(
        this.data.projectId,
        this.data.chapterId,
        workspace.sections.map((section) => ({
          id: section.id,
          version: section.version,
        })),
      );
      this.setData({ expandedSectionIds: {} });
      await this.loadPageData(true);
      if (result.unassigned_count > 0) {
        wx.showModal({
          title: "有讲述需要选择小节",
          content: `已整理为 ${result.section_count} 个小节；另有 ${result.unassigned_count} 条讲述放在“待归类素材”中，请在本页逐条选择。`,
          showCancel: false,
          confirmText: "去选择",
          success: (modalResult) => {
            if (modalResult.confirm) {
              void this.runNextAction();
            }
          },
        });
      } else {
        wx.showToast({
          title: `已整理为${result.section_count}个小节`,
          icon: "success",
        });
      }
    } catch (error) {
      if (routeAuthError(error, "login")) {
        return;
      }
      this.setData({
        error: error instanceof Error ? error.message : "小节重新生成失败",
      });
    } finally {
      this.setData({ regeneratingSections: false });
    }
  },

  async runNextAction() {
    const action = this.data.nextAction;
    if (
      action.disabled
      || this.data.processBusy
      || Boolean(this.data.startingIssueId)
      || Boolean(this.data.regeneratingDraftSectionId)
    ) {
      return;
    }
    if (action.kind === "prepare") {
      await this.prepareChapter();
      return;
    }
    if (action.kind === "start_interview") {
      await this.beginCompletionInterview();
      return;
    }
    if (action.kind === "confirm") {
      this.confirmCurrentIssue();
      return;
    }
    if (action.kind === "review_sections") {
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
              this.data.expandedDraftSectionIds,
            )
            : {}),
        });
      }
      scrollToSection("#chapter-structure");
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
    const targets = this.data.sectionViews.filter(
      (section) => (
        !section.requires_user_assignment
        && section.status === "active"
        && section.include_in_book !== false
      ),
    );
    const target = targets[targetIndex];
    if (
      !utteranceId
      || !target
      || this.data.movingMaterialId
      || this.data.processBusy
      || this.data.regeneratingSections
      || this.data.rewritingDraft
      || Boolean(this.data.regeneratingDraftSectionId)
    ) {
      return;
    }
    wx.showModal({
      title: "确认素材归属",
      content: `把这条讲述移到“${target.title}”？`,
      confirmText: "确认移动",
      cancelText: "再想想",
      success: (result) => {
        if (result.confirm) {
          void this.performMaterialMove(utteranceId, target.id);
        }
      },
    });
  },

  async performMaterialMove(
    utteranceId: string,
    targetSectionId: string,
  ) {
    if (!utteranceId || !targetSectionId || this.data.movingMaterialId) {
      return;
    }
    this.setData({ movingMaterialId: utteranceId, error: "" });
    try {
      await moveMemoirChapterMaterial(
        this.data.projectId,
        utteranceId,
        targetSectionId,
      );
      await this.loadPageData(true);
      wx.showToast({ title: "已选择小节", icon: "success" });
    } catch (error) {
      if (routeAuthError(error, "login")) {
        return;
      }
      this.setData({
        error: error instanceof Error
          ? error.message
          : "素材移动失败",
      });
    } finally {
      this.setData({ movingMaterialId: "" });
    }
  },

  async runDraftRecoveryAction() {
    const workspace = this.data.workspace;
    if (
      !workspace?.draft_content
      || workspace.draft_sections.length > 0
      || this.data.draftRecoveryActionDisabled
      || this.data.processBusy
      || this.data.rewritingDraft
      || Boolean(this.data.regeneratingDraftSectionId)
      || this.data.confirmingChapter
      || this.data.savingDraft
      || Boolean(this.data.movingMaterialId)
      || Boolean(this.data.startingIssueId)
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
    if (this.data.processBusy || this.data.regeneratingDraftSectionId) {
      return;
    }
    this.stopPreparationPolling();
    this.setData({ processBusy: true, error: "" });
    try {
      await prepareMemoirBookChapter(this.data.projectId, this.data.chapterId);
      await this.loadPageData(true);
      this.schedulePreparationPolling();
    } catch (error) {
      if (routeAuthError(error, "login")) {
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "本章整理失败" });
    } finally {
      this.setData({ processBusy: false });
    }
  },

  async beginCompletionInterview() {
    const issue = this.data.workspace?.completion_summary.active_issue;
    if (!issue) {
      this.setData({ error: "补充问题还没有准备好，请下拉刷新" });
      return;
    }
    if (issue.status === "in_progress" && issue.active_session_id) {
      this.openVoiceChat(issue.active_session_id);
      return;
    }
    if (!readVoiceAuthorizationConsent()) {
      wx.navigateTo({ url: VOICE_AUTHORIZATION_PAGE_URL });
      return;
    }
    this.setData({ startingIssueId: issue.id, error: "" });
    try {
      await syncVoiceAuthorizationConsent();
      const detail = await createMemoirVoiceSession({
        guidance_mode: "guided_interview",
        interview_type: "ai_interview",
        session_goal: "book_completion",
        current_completion_issue_id: issue.id,
        provider: "volc_streaming_asr",
        input_format: "pcm_s16le_16000_mono",
        metadata_json: {
          entry: "miniprogram_chapter_workspace",
          chapter_id: this.data.chapterId,
          completion_issue_id: issue.id,
        },
      }, { projectId: this.data.projectId });
      trackAuditAction(
        "voice_chat.start",
        {
          guidance_mode: "guided_interview",
          interview_type: "ai_interview",
          session_goal: "book_completion",
          entry: "chapter_workspace",
        },
        { projectId: this.data.projectId, voiceSessionId: detail.session.id },
      );
      this.openVoiceChat(detail.session.id);
    } catch (error) {
      if (routeAuthError(error, "login")) {
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "AI 访谈创建失败" });
    } finally {
      this.setData({ startingIssueId: "" });
    }
  },

  openVoiceChat(voiceSessionId: string) {
    wx.navigateTo({
      url: pageUrl("/pages/voice-chat/index", {
        voiceSessionId,
        mode: "guided_interview",
        interviewType: "ai_interview",
        sessionGoal: "book_completion",
        projectTitle: this.data.workspace?.chapter_title || "",
      }),
    });
  },

  confirmCurrentIssue() {
    const issue = this.data.workspace?.completion_summary.active_issue;
    if (!issue || !this.data.nextAction.issueId || this.data.processBusy) {
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

  async applyCurrentIssueConfirmation(issueId: string) {
    if (!issueId || this.data.processBusy) {
      return;
    }
    this.setData({ processBusy: true, error: "" });
    try {
      await closeMemoirCompletionIssue(this.data.projectId, issueId, {
        outcome: "confirmed",
        note: "用户已确认按当前建议处理",
      });
      await prepareMemoirBookChapter(this.data.projectId, this.data.chapterId);
      await this.loadPageData(true);
      this.schedulePreparationPolling();
    } catch (error) {
      if (routeAuthError(error, "login")) {
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "确认失败" });
    } finally {
      this.setData({ processBusy: false });
    }
  },

  startDraftSectionEdit(event: PageDatasetEvent<{ id?: string }>) {
    const workspace = this.data.workspace;
    const sectionId = datasetString(event, "id");
    const section = workspace?.draft_sections.find(
      (item) => item.section_id === sectionId,
    );
    if (!workspace?.draft_version_id || !section) {
      return;
    }
    if (
      this.data.regeneratingDraftSectionId
      || this.data.regeneratingSections
      || this.data.rewritingDraft
      || this.data.confirmingChapter
      || Boolean(this.data.editingSectionId)
    ) {
      return;
    }
    this.stopDraftSectionPlayback();
    const expandedDraftSectionIds = {
      ...this.data.expandedDraftSectionIds,
      [section.section_id]: true,
    };
    this.setData({
      expandedDraftSectionIds,
      ...chapterWorkspaceViewPatch(
        workspace,
        this.data.expandedSectionIds,
        expandedDraftSectionIds,
      ),
      editingSectionId: section.section_id,
      editingSectionContent: section.content,
    });
  },

  changeDraftSectionContent(event: PageTextInputEvent) {
    this.setData({ editingSectionContent: event.detail.value });
  },

  cancelDraftSectionEdit() {
    this.setData({ editingSectionId: "", editingSectionContent: "" });
  },

  async saveDraftSectionEdit() {
    const workspace = this.data.workspace;
    const sectionId = this.data.editingSectionId;
    const content = this.data.editingSectionContent.trim();
    const currentSection = workspace?.draft_sections.find(
      (section) => section.section_id === sectionId,
    );
    if (
      !workspace?.draft_version_id
      || !currentSection
      || this.data.savingDraft
      || Boolean(this.data.movingMaterialId)
    ) {
      return;
    }
    if (!content) {
      wx.showToast({ title: "小节正文不能为空", icon: "none" });
      return;
    }
    if (content === currentSection.content.trim()) {
      this.cancelDraftSectionEdit();
      return;
    }
    this.setData({ savingDraft: true, error: "" });
    try {
      await reviseMemoirBookChapterDraft(
        this.data.projectId,
        this.data.chapterId,
        {
          expected_version_id: workspace.draft_version_id,
          sections: workspace.draft_sections.map((section) => ({
            section_id: section.section_id,
            content: section.section_id === sectionId
              ? content
              : section.content,
          })),
        },
      );
      this.setData({ editingSectionId: "", editingSectionContent: "" });
      await this.loadPageData(true);
      wx.showToast({ title: "小节已保存", icon: "success" });
    } catch (error) {
      if (routeAuthError(error, "login")) {
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "正文保存失败" });
    } finally {
      this.setData({ savingDraft: false });
    }
  },

  async confirmChapter() {
    if (
      !this.data.canConfirmChapter
      || this.data.confirmingChapter
      || Boolean(this.data.regeneratingDraftSectionId)
      || Boolean(this.data.editingSectionId)
    ) {
      return;
    }
    this.setData({ confirmingChapter: true, error: "" });
    try {
      await confirmMemoirBookChapterDraft(this.data.projectId, this.data.chapterId);
      await this.loadPageData(true);
      wx.showToast({ title: "本章正文已确认", icon: "success" });
    } catch (error) {
      if (routeAuthError(error, "login")) {
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "本章正文确认失败" });
    } finally {
      this.setData({ confirmingChapter: false });
    }
  },

  regenerateDraftSection(event: PageDatasetEvent<{ id?: string }>) {
    const sectionId = datasetString(event, "id");
    const workspace = this.data.workspace;
    const section = workspace?.draft_sections.find(
      (item) => item.section_id === sectionId,
    );
    if (!workspace?.draft_version_id || !section) {
      return;
    }
    if (workspace.draft_needs_rewrite) {
      wx.showToast({
        title: "请先更新全部小节正文",
        icon: "none",
      });
      return;
    }
    if (
      this.data.regeneratingDraftSectionId
      || this.data.regeneratingSections
      || this.data.rewritingDraft
      || this.data.processBusy
      || this.data.confirmingChapter
      || this.data.savingDraft
      || Boolean(this.data.editingSectionId)
    ) {
      return;
    }
    wx.showModal({
      title: `重新生成“${section.title}”？`,
      content: "只会替换这一小节的正文，其他小节保持不变；当前正文仍会保留在历史版本中。",
      confirmText: "重新生成",
      cancelText: "暂不生成",
      success: (result) => {
        if (result.confirm) {
          void this.performDraftSectionRegeneration(section.section_id);
        }
      },
    });
  },

  async performDraftSectionRegeneration(sectionId: string) {
    const workspace = this.data.workspace;
    if (
      !workspace?.draft_version_id
      || !sectionId
      || this.data.regeneratingDraftSectionId
    ) {
      return;
    }
    this.stopDraftSectionPlayback();
    this.setData({
      regeneratingDraftSectionId: sectionId,
      error: "",
    });
    try {
      await regenerateMemoirChapterDraftSection(
        this.data.projectId,
        this.data.chapterId,
        sectionId,
        workspace.draft_version_id,
      );
      const expandedDraftSectionIds = {
        ...this.data.expandedDraftSectionIds,
        [sectionId]: true,
      };
      this.setData({ expandedDraftSectionIds });
      await this.loadPageData(true);
      wx.showToast({ title: "这一小节已更新", icon: "success" });
    } catch (error) {
      if (routeAuthError(error, "login")) {
        return;
      }
      this.setData({
        error: error instanceof Error
          ? error.message
          : "小节正文重新生成失败",
      });
    } finally {
      this.setData({ regeneratingDraftSectionId: "" });
    }
  },

  async playDraftSection(event: PageDatasetEvent<{ id?: string }>) {
    const sectionId = datasetString(event, "id");
    const section = this.data.workspace?.draft_sections.find(
      (item) => item.section_id === sectionId,
    );
    if (!section || !this.data.projectId || !this.data.chapterId) {
      return;
    }
    if (
      this.data.playingSectionId === sectionId
      || this.data.ttsBusySectionId === sectionId
    ) {
      this.stopDraftSectionPlayback();
      return;
    }
    if (
      this.data.ttsBusySectionId
      || Boolean(this.data.regeneratingDraftSectionId)
      || this.data.regeneratingSections
      || this.data.rewritingDraft
      || this.data.processBusy
    ) {
      return;
    }
    this.stopDraftSectionPlayback();
    const token = this.audioPlayback.nextToken();
    this.playToken = token;
    this.setData({ ttsBusySectionId: sectionId, error: "" });
    const settings = readBookTTSSettings();
    try {
      const response = await createMemoirBookChapterDraftSectionTTS(
        this.data.projectId,
        this.data.chapterId,
        sectionId,
        {
          ...(settings.voice ? { voice: settings.voice } : {}),
          ...(settings.speedRatio === 1
            ? {}
            : { speed_ratio: settings.speedRatio }),
        },
      );
      const audioFileIds = response.audio_file_ids || [];
      if (!audioFileIds.length) {
        throw new Error("没有可播放的音频");
      }
      if (token !== this.playToken) {
        return;
      }
      this.setData({
        ttsBusySectionId: "",
        playingSectionId: sectionId,
      });
      for (const audioFileId of audioFileIds) {
        if (token !== this.playToken) {
          return;
        }
        await this.audioPlayback.playFile(audioFileId, token, {
          errorMessage: "小节朗读失败",
        });
      }
    } catch (error) {
      if (routeAuthError(error, "login")) {
        return;
      }
      if (token === this.playToken) {
        this.setData({
          error: error instanceof Error ? error.message : "小节朗读失败",
        });
      }
    } finally {
      if (token === this.playToken) {
        this.setData({
          ttsBusySectionId: "",
          playingSectionId: "",
        });
      }
    }
  },

  stopDraftSectionPlayback() {
    this.audioPlayback.stop();
    this.playToken = this.audioPlayback.currentToken();
    if (this.data.ttsBusySectionId || this.data.playingSectionId) {
      this.setData({
        ttsBusySectionId: "",
        playingSectionId: "",
      });
    }
  },

  rewriteChapter() {
    const workspace = this.data.workspace;
    if (
      !workspace?.draft_needs_rewrite
      || !workspace.can_rewrite_draft
      || this.data.rewritingDraft
      || this.data.processBusy
      || Boolean(this.data.startingIssueId)
      || this.data.confirmingChapter
      || this.data.savingDraft
      || Boolean(this.data.regeneratingDraftSectionId)
      || Boolean(this.data.editingSectionId)
    ) {
      return;
    }
    wx.showModal({
      title: this.data.rewriteDraftActionText,
      content: this.data.rewriteDraftScopeDetail,
      confirmText: "生成全部",
      cancelText: "暂不生成",
      success: (result) => {
        if (result.confirm) {
          void this.performChapterRewrite();
        }
      },
    });
  },

  async performChapterRewrite() {
    if (this.data.rewritingDraft) {
      return;
    }
    this.stopDraftSectionPlayback();
    this.setData({ rewritingDraft: true, error: "" });
    try {
      await rewriteMemoirBookChapter(
        this.data.projectId,
        this.data.chapterId,
      );
      await this.loadPageData(true);
      wx.showToast({ title: "小节正文已更新", icon: "success" });
    } catch (error) {
      if (routeAuthError(error, "login")) {
        return;
      }
      this.setData({
        error: error instanceof Error ? error.message : "本章重新生成失败",
      });
    } finally {
      this.setData({ rewritingDraft: false });
    }
  },

  schedulePreparationPolling() {
    if (
      !this.pageVisible
      || this.preparationPollTimer !== null
      || this.data.workspace?.completion_summary.state !== "preparing"
    ) {
      return;
    }
    this.preparationPollTimer = setTimeout(() => {
      this.preparationPollTimer = null;
      if (this.pageVisible) {
        void this.loadPageData(true);
      }
    }, PREPARATION_POLL_INTERVAL_MS);
  },

  stopPreparationPolling() {
    if (this.preparationPollTimer !== null) {
      clearTimeout(this.preparationPollTimer);
      this.preparationPollTimer = null;
    }
  },
});
