import type { MemoirChapterWorkspace } from "../../domain/chapter-workspace";
import { trackAuditAction } from "../../services/audit-tracker";
import { definePage } from "../../services/page-definition";
import {
  assembleMemoirChapterDraftBlocks,
  confirmMemoirBookChapterDraft,
  confirmMemoirChapterFact,
  createMemoirVoiceSession,
  getMemoirChapterWorkspace,
  listMemoirDraftBlocks,
  manuallyReviseMemoirDraftBlock,
  prepareMemoirBookChapter,
  unlockMemoirDraftBlock,
} from "../../services/memoir-voice-api";
import { routeAuthError } from "../../services/navigation";
import {
  datasetString,
  pageUrl,
  queryParam,
  type PageDatasetEvent,
  type PageQuery,
  type PageTextInputEvent,
} from "../../services/page-input";
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
  draftBlockViews,
  type ConfirmationView,
  type DraftBlockView,
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

interface WorkspaceDataset {
  blockId?: string;
}

type DatasetEvent = PageDatasetEvent<WorkspaceDataset>;

interface ChapterWorkspacePageData {
  loading: boolean;
  error: string;
  projectId: string;
  chapterId: string;
  workspace: MemoirChapterWorkspace | null;
  nextAction: ChapterNextAction;
  readinessTitle: string;
  readinessDetail: string;
  confirmation: ConfirmationView | null;
  draftBlocks: DraftBlockView[];
  canConfirmChapter: boolean;
  processBusy: boolean;
  startingTaskId: string;
  confirmingFact: boolean;
  confirmingChapter: boolean;
  editingBlockId: string;
  editingBlockContent: string;
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
    confirmation: null,
    draftBlocks: [],
    canConfirmChapter: false,
    processBusy: false,
    startingTaskId: "",
    confirmingFact: false,
    confirmingChapter: false,
    editingBlockId: "",
    editingBlockContent: "",
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
      const [workspace, blockList] = await Promise.all([
        getMemoirChapterWorkspace(this.data.projectId, this.data.chapterId),
        listMemoirDraftBlocks(this.data.projectId, this.data.chapterId),
      ]);
      const draftBlocks = draftBlockViews(blockList.blocks, this.data.draftBlocks);
      this.setData({
        workspace,
        draftBlocks,
        ...chapterWorkspaceViewPatch(
          workspace,
          draftBlocks,
          this.data.confirmation,
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
      if (routeAuthError(error, "me")) {
        return;
      }
      this.setData({
        loading: false,
        error: error instanceof Error ? error.message : "本章内容加载失败",
      });
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
    if (action.kind === "start_interview" || action.kind === "continue_confirmation") {
      await this.beginFollowupInterview();
      return;
    }
    if (action.kind === "confirm_fact") {
      scrollToSection("#chapter-confirmation");
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
      if (routeAuthError(error, "me")) {
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "本章整理失败" });
    } finally {
      this.setData({ processBusy: false });
    }
  },

  async beginFollowupInterview() {
    const task = this.data.workspace?.completion_summary.active_task;
    if (!task) {
      this.setData({ error: "补充问题还没有准备好，请下拉刷新" });
      return;
    }
    if (task.status === "in_progress" && task.active_session_id) {
      this.openVoiceChat(task.active_session_id);
      return;
    }
    if (!readVoiceAuthorizationConsent()) {
      wx.navigateTo({ url: VOICE_AUTHORIZATION_PAGE_URL });
      return;
    }
    this.setData({ startingTaskId: task.id, error: "" });
    try {
      await syncVoiceAuthorizationConsent();
      const detail = await createMemoirVoiceSession({
        guidance_mode: "guided_interview",
        interview_type: "ai_interview",
        session_goal: "book_completion",
        current_followup_task_id: task.id,
        provider: "volc_streaming_asr",
        input_format: "pcm_s16le_16000_mono",
        metadata_json: {
          entry: "miniprogram_chapter_workspace",
          chapter_id: this.data.chapterId,
          followup_task_id: task.id,
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
      if (routeAuthError(error, "me")) {
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "AI 访谈创建失败" });
    } finally {
      this.setData({ startingTaskId: "" });
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

  changeConfirmationStatement(event: PageTextInputEvent) {
    if (!this.data.confirmation) {
      return;
    }
    this.setData({
      confirmation: {
        ...this.data.confirmation,
        statement: event.detail.value,
      },
    });
  },

  async saveConfirmation() {
    const confirmation = this.data.confirmation;
    const statement = confirmation?.statement.trim() || "";
    if (!confirmation || !statement || this.data.confirmingFact) {
      if (!statement) {
        wx.showToast({ title: "请先写下准确说法", icon: "none" });
      }
      return;
    }
    this.setData({ confirmingFact: true, error: "" });
    try {
      await confirmMemoirChapterFact(this.data.projectId, confirmation.id, statement);
      await prepareMemoirBookChapter(this.data.projectId, this.data.chapterId);
      this.setData({ confirmation: null });
      await this.loadPageData(true);
      this.schedulePreparationPolling();
    } catch (error) {
      if (routeAuthError(error, "me")) {
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "确认内容保存失败" });
    } finally {
      this.setData({ confirmingFact: false });
    }
  },

  startDraftBlockEdit(event: DatasetEvent) {
    const blockId = datasetString(event, "blockId");
    const block = this.data.draftBlocks.find((item) => item.id === blockId);
    if (!block || block.locked || block.freshness_status !== "current") {
      return;
    }
    this.setData({
      editingBlockId: block.id,
      editingBlockContent: block.current_version.content,
    });
  },

  changeDraftBlockContent(event: PageTextInputEvent) {
    this.setData({ editingBlockContent: event.detail.value });
  },

  cancelDraftBlockEdit() {
    this.setData({ editingBlockId: "", editingBlockContent: "" });
  },

  patchDraftBlock(blockId: string, patch: Partial<DraftBlockView>) {
    this.setData({
      draftBlocks: this.data.draftBlocks.map(
        (item) => item.id === blockId ? { ...item, ...patch } : item,
      ),
    });
  },

  async saveDraftBlockEdit(event: DatasetEvent) {
    const blockId = datasetString(event, "blockId");
    const block = this.data.draftBlocks.find((item) => item.id === blockId);
    const content = this.data.editingBlockContent.trim();
    if (!block || !content || block.busy) {
      if (!content) {
        wx.showToast({ title: "正文不能为空", icon: "none" });
      }
      return;
    }
    this.patchDraftBlock(block.id, { busy: true });
    try {
      await manuallyReviseMemoirDraftBlock(this.data.projectId, block.id, {
        expected_current_version_id: block.current_version_id,
        content,
        feedback: "用户在章节正文页直接修改",
      });
      this.setData({ editingBlockId: "", editingBlockContent: "" });
      await this.loadPageData(true);
    } catch (error) {
      if (routeAuthError(error, "me")) {
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "正文保存失败" });
    } finally {
      this.patchDraftBlock(block.id, { busy: false });
    }
  },

  async unlockLegacyBlock(event: DatasetEvent) {
    const blockId = datasetString(event, "blockId");
    const block = this.data.draftBlocks.find((item) => item.id === blockId);
    if (!block || !block.locked || block.busy) {
      return;
    }
    this.patchDraftBlock(block.id, { busy: true });
    try {
      await unlockMemoirDraftBlock(this.data.projectId, block.id, {
        expected_current_version_id: block.current_version_id,
        idempotency_key: `chapter-unlock:${block.id}:${Date.now()}`,
        reason: "迁移到简化章节确认流程",
      });
      await this.loadPageData(true);
    } catch (error) {
      if (routeAuthError(error, "me")) {
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "正文解除保护失败" });
    } finally {
      this.patchDraftBlock(block.id, { busy: false });
    }
  },

  async confirmChapter() {
    if (
      !this.data.canConfirmChapter
      || this.data.confirmingChapter
      || this.data.editingBlockId
    ) {
      return;
    }
    this.setData({ confirmingChapter: true, error: "" });
    try {
      await assembleMemoirChapterDraftBlocks(this.data.projectId, this.data.chapterId);
      await confirmMemoirBookChapterDraft(this.data.projectId, this.data.chapterId);
      await this.loadPageData(true);
      wx.showToast({ title: "本章正文已确认", icon: "success" });
    } catch (error) {
      if (routeAuthError(error, "me")) {
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "本章正文确认失败" });
    } finally {
      this.setData({ confirmingChapter: false });
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

  goBack() {
    wx.navigateBack();
  },
});
