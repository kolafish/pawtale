import type { MemoirChapterWorkspace } from "../../domain/chapter-workspace";
import { trackAuditAction } from "../../services/audit-tracker";
import { definePage } from "../../services/page-definition";
import {
  closeMemoirCompletionIssue,
  confirmMemoirBookChapterDraft,
  createMemoirVoiceSession,
  getMemoirChapterWorkspace,
  prepareMemoirBookChapter,
  regenerateMemoirChapterSections,
  rewriteMemoirBookChapter,
  reviseMemoirBookChapterDraft,
} from "../../services/memoir-voice-api";
import { routeAuthError } from "../../services/navigation";
import {
  pageUrl,
  datasetString,
  queryParam,
  type PageDatasetEvent,
  type PageQuery,
  type PageTextInputEvent,
} from "../../services/page-input";
import { setPageNavigationTitle } from "../../services/page-navigation";
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
  sectionStructureSummary: string;
  sectionViews: ChapterSectionView[];
  expandedSectionIds: Record<string, boolean>;
  canConfirmChapter: boolean;
  processBusy: boolean;
  regeneratingSections: boolean;
  rewritingDraft: boolean;
  startingIssueId: string;
  confirmingChapter: boolean;
  savingDraft: boolean;
  editingSectionId: string;
  editingSectionContent: string;
}

definePage<ChapterWorkspacePageData>()({
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
    sectionStructureSummary: "",
    sectionViews: [],
    expandedSectionIds: {},
    canConfirmChapter: false,
    processBusy: false,
    regeneratingSections: false,
    rewritingDraft: false,
    startingIssueId: "",
    confirmingChapter: false,
    savingDraft: false,
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
  },

  onUnload() {
    this.pageVisible = false;
    this.stopPreparationPolling();
  },

  async onPullDownRefresh() {
    await this.loadPageData(true);
    wx.stopPullDownRefresh();
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
        ...chapterWorkspaceViewPatch(workspace, this.data.expandedSectionIds),
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
    const expandedSectionIds = {
      ...this.data.expandedSectionIds,
      [sectionId]: !this.data.expandedSectionIds[sectionId],
    };
    this.setData({
      expandedSectionIds,
      ...chapterWorkspaceViewPatch(workspace, expandedSectionIds),
    });
  },

  regenerateSections() {
    const workspace = this.data.workspace;
    if (
      !workspace?.sections.length
      || this.data.regeneratingSections
      || this.data.processBusy
      || this.data.rewritingDraft
      || this.data.confirmingChapter
      || this.data.savingDraft
      || Boolean(this.data.editingSectionId)
    ) {
      return;
    }
    wx.showModal({
      title: "重新生成本章小节",
      content: "AI 会重新划分本章讲述的分组和顺序。原始讲述、是否入书的选择和现有正文版本都会保留，完成后需要按新结构重新生成正文。",
      confirmText: "开始生成",
      cancelText: "先不生成",
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
          title: "小节已重新生成",
          content: `生成了 ${result.section_count} 个小节；另有 ${result.unassigned_count} 条讲述暂未归类，可以到记录页继续处理。`,
          showCancel: false,
          confirmText: "知道了",
        });
      } else {
        wx.showToast({
          title: `已生成${result.section_count}个小节`,
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
    if (action.disabled || this.data.processBusy) {
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
    if (action.kind === "review_draft" || action.kind === "complete") {
      scrollToSection("#chapter-draft");
    }
  },

  async prepareChapter() {
    if (this.data.processBusy) {
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
    this.setData({
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
      || Boolean(this.data.editingSectionId)
    ) {
      return;
    }
    wx.showModal({
      title: "按现有素材重写",
      content: "会重新生成一版完整章节，当前正文会保留在历史版本中。",
      confirmText: "开始重写",
      cancelText: "先不重写",
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
    this.setData({ rewritingDraft: true, error: "" });
    try {
      await rewriteMemoirBookChapter(
        this.data.projectId,
        this.data.chapterId,
      );
      await this.loadPageData(true);
      wx.showToast({ title: "本章正文已更新", icon: "success" });
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
