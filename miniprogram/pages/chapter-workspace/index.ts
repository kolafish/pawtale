import type { MemoirChapterWorkspace } from "../../domain/chapter-workspace";
import { trackAuditAction } from "../../services/audit-tracker";
import {
  AudioPlaybackController,
  playAudioFileSequence,
} from "../../services/audio-playback";
import { definePage } from "../../services/page-definition";
import {
  closeMemoirCompletionIssue,
  cancelMemoirChapterPreparationJob,
  cancelMemoirChapterSectionRegenerationJob,
  confirmMemoirBookChapterDraft,
  createMemoirBookChapterDraftSectionTTS,
  createMemoirVoiceSession,
  enqueueMemoirChapterPreparationJob,
  getMemoirChapterPreparationJob,
  getMemoirChapterSectionRegenerationJob,
  getMemoirChapterWorkspace,
  placeMemoirChapterMaterial,
  reviseMemoirChapterDraftSectionWithAI,
  regenerateMemoirChapterSections,
  reviseMemoirBookChapterDraft,
  type MemoirChapterPreparationJob,
  type MemoirSectionMaterialPlacementRequest,
  type MemoirChapterSectionRegenerationJob,
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
import { readBookTTSRequest } from "../../services/tts-settings";
import {
  recordMemoirVoiceSearchQuery,
  stopMemoirVoiceSearchRecording,
} from "../../services/memoir-voice-search";
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
import {
  readCachedMemoirChapterWorkspace,
  writeCachedMemoirChapterWorkspace,
} from "../../stores/memoir-read-cache";
import { promptRequiredText } from "../../services/text-prompt";
import {
  aiTaskProgressPercent,
  type AITaskProgressMode,
  type AITaskProgressView,
} from "../../services/ai-task-progress";

export { chapterWorkspaceViewPatch };

const PREPARATION_POLL_INTERVAL_MS = 1500;
const SECTION_REGENERATION_POLL_INTERVAL_MS = 1500;
const DRAFT_SECTION_VOICE_INSTRUCTION_MAX_MS = 30000;

type ChapterWorkspaceOperationKind =
  | ""
  | "prepare"
  | "regenerate_sections"
  | "move_material"
  | "start_interview"
  | "confirm_issue"
  | "save_draft_section"
  | "confirm_chapter"
  | "revise_draft_section_with_ai"
  | "rewrite_chapter";

type ChapterWorkspaceAudioPhase = "idle" | "preparing" | "playing";
type DraftSectionEditingMode = "" | "manual" | "ai";
type DraftSectionVoicePhase =
  | "idle"
  | "listening"
  | "transcribing";

function isActiveSectionRegeneration(
  job: MemoirChapterSectionRegenerationJob | null,
): boolean {
  return job?.status === "pending" || job?.status === "running";
}

function isActiveChapterPreparation(
  job: MemoirChapterPreparationJob | null,
): boolean {
  return job?.status === "pending" || job?.status === "running";
}

function chapterPreparationView(
  job: MemoirChapterPreparationJob,
): AITaskProgressView {
  const model = job.resolved_ai_model || job.requested_ai_model;
  const modelDetail = model ? ` · 模型 ${model}` : "";
  if (job.status === "failed") {
    const savedDetail = job.completed_section_count
      ? ` · 已保留 ${job.completed_section_count}/${job.total_section_count} 个小节`
      : "";
    return {
      title: job.operation === "rewrite" ? "正文生成失败" : "本章检查失败",
      detail: `${job.error_message || "可以重新开始"}${savedDetail}${modelDetail}`,
      progress: 0,
      progressMode: "hidden",
    };
  }
  if (job.status === "cancelled") {
    return {
      title: "已取消处理",
      detail: `原有章节和正文保持不变${modelDetail}`,
      progress: 0,
      progressMode: "hidden",
    };
  }
  if (job.stage === "checking_sections") {
    const progress = aiTaskProgressPercent(
      job.checked_section_count,
      job.total_section_count,
    );
    return {
      title: "正在逐节检查素材",
      detail: `已检查 ${job.checked_section_count}/${job.total_section_count} 个小节${job.current_section_title ? ` · ${job.current_section_title}` : ""}${modelDetail}`,
      progress,
      progressMode: "determinate",
    };
  }
  if (job.stage === "checking_chapter") {
    return {
      title: "正在检查整章结构",
      detail: `小节检查已完成，正在判断是否需要补充${modelDetail}`,
      progress: 40,
      progressMode: "indeterminate",
    };
  }
  if (job.stage === "generating_sections") {
    const progress = aiTaskProgressPercent(
      job.completed_section_count,
      job.total_section_count,
    );
    const resumed = job.resumed_section_count
      ? ` · 已恢复 ${job.resumed_section_count} 节`
      : "";
    return {
      title: "正在逐节生成正文",
      detail: `已完成 ${job.completed_section_count}/${job.total_section_count} 个小节${job.current_section_title ? ` · ${job.current_section_title}` : ""}${resumed}${modelDetail}`,
      progress,
      progressMode: "determinate",
    };
  }
  if (job.stage === "saving") {
    return {
      title: "正在保存本章正文",
      detail: `全部小节已经处理完成，正在更新章节版本${modelDetail}`,
      progress: 0,
      progressMode: "indeterminate",
    };
  }
  return {
    title: job.operation === "rewrite" ? "正文生成任务已排队" : "本章检查任务已排队",
    detail: `即将处理 ${job.total_section_count} 个小节${modelDetail}`,
    progress: 0,
    progressMode: "indeterminate",
  };
}

function sectionRegenerationView(
  job: MemoirChapterSectionRegenerationJob,
): AITaskProgressView {
  const model = job.resolved_ai_model || job.requested_ai_model;
  const modelDetail = model ? ` · 模型 ${model}` : "";
  if (job.status === "failed") {
    return {
      title: "小节整理失败",
      detail: `${job.error_message || "请稍后重新整理"}${modelDetail}`,
      progress: 0,
      progressMode: "hidden",
    };
  }
  if (job.status === "cancelled") {
    return {
      title: "已取消整理",
      detail: "原小节结构保持不变",
      progress: 0,
      progressMode: "hidden",
    };
  }
  if (job.stage === "planning_sections") {
    return {
      title: "正在规划小节结构",
      detail: `正在理解全章 ${job.material_count} 条讲述${modelDetail}`,
      progress: 0,
      progressMode: "indeterminate",
    };
  }
  if (job.stage === "assigning_materials") {
    const progress = aiTaskProgressPercent(
      job.processed_char_count,
      job.total_char_count,
    );
    return {
      title: "正在归类讲述",
      detail: `已处理 ${job.processed_material_count}/${job.material_count} 条 · ${job.completed_batch_count}/${job.batch_count} 批${modelDetail}`,
      progress,
      progressMode: "determinate",
    };
  }
  if (job.stage === "saving") {
    return {
      title: "正在保存新的小节结构",
      detail: `全部讲述已归类，正在进行一致性检查${modelDetail}`,
      progress: 0,
      progressMode: "indeterminate",
    };
  }
  return {
    title: "小节整理任务已排队",
    detail: `即将开始处理 ${job.material_count} 条讲述${modelDetail}`,
    progress: 0,
    progressMode: "indeterminate",
  };
}

const CLOSED_DRAFT_SECTION_EDITOR = {
  editingMode: "" as DraftSectionEditingMode,
  editingSectionId: "",
  editingSectionContent: "",
  draftSectionVoicePhase: "idle" as DraftSectionVoicePhase,
  draftSectionAIInstruction: "",
};

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
  structureExpanded: boolean;
  sectionViews: ChapterSectionView[];
  draftSectionViews: ChapterDraftSectionView[];
  expandedSectionIds: Record<string, boolean>;
  expandedDraftSectionIds: Record<string, boolean>;
  canConfirmChapter: boolean;
  operationKind: ChapterWorkspaceOperationKind;
  operationTargetId: string;
  chapterPreparationJob: MemoirChapterPreparationJob | null;
  chapterPreparationTitle: string;
  chapterPreparationDetail: string;
  chapterPreparationProgress: number;
  chapterPreparationProgressMode: AITaskProgressMode;
  sectionRegenerationJob: MemoirChapterSectionRegenerationJob | null;
  sectionRegenerationTitle: string;
  sectionRegenerationDetail: string;
  sectionRegenerationProgress: number;
  sectionRegenerationProgressMode: AITaskProgressMode;
  audioPhase: ChapterWorkspaceAudioPhase;
  audioSectionId: string;
  editingMode: DraftSectionEditingMode;
  editingSectionId: string;
  editingSectionContent: string;
  draftSectionVoicePhase: DraftSectionVoicePhase;
  draftSectionAIInstruction: string;
}

definePage<ChapterWorkspacePageData>()({
  audioPlayback: new AudioPlaybackController(),
  playToken: 0,
  preparationPollTimer: null as ReturnType<typeof setTimeout> | null,
  sectionRegenerationPollTimer: null as ReturnType<typeof setTimeout> | null,
  pageVisible: false,
  loadedOnce: false,
  workspaceLoadSequence: 0,
  chapterPreparationLoadSequence: 0,
  sectionRegenerationLoadSequence: 0,
  draftSectionVoiceInstructionToken: 0,

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
    structureExpanded: false,
    sectionViews: [],
    draftSectionViews: [],
    expandedSectionIds: {},
    expandedDraftSectionIds: {},
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
    audioSectionId: "",
    ...CLOSED_DRAFT_SECTION_EDITOR,
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
    await Promise.all([
      this.loadPageData(),
      this.refreshChapterPreparationJob(true),
      this.refreshSectionRegenerationJob(true),
    ]);
    this.loadedOnce = true;
  },

  async onShow() {
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
    this.cancelDraftSectionVoiceInstruction();
  },

  onUnload() {
    this.pageVisible = false;
    this.stopPreparationPolling();
    this.stopSectionRegenerationPolling();
    this.stopDraftSectionPlayback();
    this.cancelDraftSectionVoiceInstruction();
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
    ) || (
      kind === "revise_draft_section_with_ai"
      && this.data.editingMode === "ai"
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
    if (this.data.editingMode === "ai") {
      return Boolean(
        this.data.draftSectionAIInstruction.trim()
        || this.data.draftSectionVoicePhase !== "idle"
      );
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
      const workspace = await getMemoirChapterWorkspace(
        this.data.projectId,
        this.data.chapterId,
      );
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
    setPageNavigationTitle(workspace.chapter_title || "本章正文");
    this.setData({
      workspace,
      ...chapterWorkspaceViewPatch(
        workspace,
        this.data.expandedSectionIds,
        this.data.expandedDraftSectionIds,
      ),
      structureExpanded:
        !workspace.draft_sections.length
        || this.data.structureExpanded,
      loading: false,
      error: "",
    });
    if (!isActiveChapterPreparation(this.data.chapterPreparationJob)) {
      this.stopPreparationPolling();
    }
  },

  toggleStructure() {
    this.setData({
      structureExpanded: !this.data.structureExpanded,
    });
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
      || Boolean(this.data.operationKind)
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
        error: error instanceof Error ? error.message : "小节重新生成失败",
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
      this.setData({ chapterPreparationJob: null });
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
        structureExpanded: true,
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
          title: "有讲述暂未归类",
          content: `已整理为 ${job.section_count} 个小节；另有 ${job.unassigned_count} 条讲述暂不纳入本章正文。您可以稍后归类，也可以进入讲述详情编辑或删除。`,
          showCancel: true,
          confirmText: "查看素材",
          cancelText: "稍后处理",
          success: (modalResult) => {
            if (modalResult.confirm) {
              this.revealPendingAssignments();
            }
          },
        });
      } else {
        wx.showToast({
          title: `已整理为${job.section_count}个小节`,
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
        structureExpanded: true,
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
        structureExpanded: true,
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
  },

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
    if (action.kind === "start_interview") {
      await this.beginCompletionInterview();
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

  async beginCompletionInterview() {
    const issue = this.data.workspace?.completion_summary.active_issue;
    if (!issue) {
      this.setData({ error: "补充问题还没有准备好，请下拉刷新" });
      return;
    }
    if (this.data.operationKind) {
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
    if (!this.beginOperation("start_interview", issue.id)) {
      return;
    }
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
      this.finishOperation("start_interview", issue.id);
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

  async applyCurrentIssueConfirmation(issueId: string) {
    if (
      !issueId
      || !this.beginOperation("confirm_issue", issueId)
    ) {
      return;
    }
    try {
      await closeMemoirCompletionIssue(this.data.projectId, issueId, {
        outcome: "confirmed",
        note: "用户已确认按当前建议处理",
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

  openDraftSectionEditor(
    event: PageDatasetEvent<{ id?: string }>,
    editingMode: Exclude<DraftSectionEditingMode, "">,
  ) {
    const workspace = this.data.workspace;
    const sectionId = datasetString(event, "id");
    const section = workspace?.draft_sections.find(
      (item) => item.section_id === sectionId,
    );
    if (!workspace?.draft_version_id || !section) {
      return;
    }
    if (editingMode === "ai" && workspace.draft_needs_rewrite) {
      wx.showToast({
        title: "请先更新全部小节正文",
        icon: "none",
      });
      return;
    }
    if (
      this.data.operationKind
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
      editingMode,
      editingSectionId: section.section_id,
      editingSectionContent: section.content,
      draftSectionVoicePhase: "idle",
      draftSectionAIInstruction: "",
      error: "",
    });
  },

  startDraftSectionEdit(event: PageDatasetEvent<{ id?: string }>) {
    this.openDraftSectionEditor(event, "manual");
  },

  changeDraftSectionContent(event: PageTextInputEvent) {
    if (this.data.editingMode !== "manual") {
      return;
    }
    this.setData({ editingSectionContent: event.detail.value });
  },

  cancelDraftSectionEdit() {
    if (this.data.operationKind) {
      return;
    }
    this.cancelDraftSectionVoiceInstruction();
    this.setData(CLOSED_DRAFT_SECTION_EDITOR);
  },

  startDraftSectionAIEdit(event: PageDatasetEvent<{ id?: string }>) {
    this.openDraftSectionEditor(event, "ai");
  },

  changeDraftSectionAIInstruction(event: PageTextInputEvent) {
    if (
      this.data.editingMode !== "ai"
      || this.data.draftSectionVoicePhase !== "idle"
    ) {
      return;
    }
    this.setData({ draftSectionAIInstruction: event.detail.value });
  },

  cancelDraftSectionVoiceInstruction() {
    if (
      this.data.draftSectionVoicePhase !== "listening"
      && this.data.draftSectionVoicePhase !== "transcribing"
    ) {
      return;
    }
    this.draftSectionVoiceInstructionToken += 1;
    if (this.data.draftSectionVoicePhase === "listening") {
      stopMemoirVoiceSearchRecording();
    }
    this.setData({ draftSectionVoicePhase: "idle" });
  },

  async toggleDraftSectionVoiceInstruction() {
    if (this.data.draftSectionVoicePhase === "listening") {
      stopMemoirVoiceSearchRecording();
      return;
    }
    const sectionId = this.data.editingSectionId;
    if (
      this.data.editingMode !== "ai"
      || !sectionId
      || this.data.operationKind
      || this.data.draftSectionVoicePhase !== "idle"
    ) {
      return;
    }
    this.stopDraftSectionPlayback();
    this.draftSectionVoiceInstructionToken += 1;
    const requestToken = this.draftSectionVoiceInstructionToken;
    this.setData({
      draftSectionVoicePhase: "listening",
      error: "",
    });
    try {
      const instructionText = await recordMemoirVoiceSearchQuery({
        stopMode: "manual",
        maxDurationMs: DRAFT_SECTION_VOICE_INSTRUCTION_MAX_MS,
        onRecordingEnd: () => {
          if (requestToken === this.draftSectionVoiceInstructionToken) {
            this.setData({ draftSectionVoicePhase: "transcribing" });
          }
        },
        emptyTranscriptMessage: "没有识别到修改要求，请再说一次",
        noVoiceTranscriptMessage: "没有听到修改要求，请再说一次",
      });
      if (requestToken !== this.draftSectionVoiceInstructionToken) {
        return;
      }
      this.setData({
        draftSectionAIInstruction: instructionText,
        draftSectionVoicePhase: "idle",
      });
      wx.showToast({ title: "已识别修改要求", icon: "none" });
    } catch (error) {
      if (requestToken !== this.draftSectionVoiceInstructionToken) {
        return;
      }
      this.setData({
        error: error instanceof Error ? error.message : "语音修改失败",
        draftSectionVoicePhase: "idle",
      });
    }
  },

  async submitDraftSectionAIInstruction() {
    const sectionId = this.data.editingSectionId;
    const instructionText = this.data.draftSectionAIInstruction.trim();
    if (
      this.data.editingMode !== "ai"
      || !sectionId
      || this.data.operationKind
      || this.data.draftSectionVoicePhase !== "idle"
    ) {
      return;
    }
    if (!instructionText) {
      wx.showToast({ title: "请先说出或输入修改要求", icon: "none" });
      return;
    }
    this.setData({ error: "" });
    await this.performDraftSectionAIRevision(
      sectionId,
      instructionText,
    );
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
      || this.data.editingMode !== "manual"
      || Boolean(this.data.operationKind)
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
    if (!this.beginOperation("save_draft_section", sectionId)) {
      return;
    }
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
      this.setData(CLOSED_DRAFT_SECTION_EDITOR);
      await this.loadPageData(true);
      wx.showToast({ title: "小节已保存", icon: "success" });
    } catch (error) {
      if (routeAuthError(error, "login")) {
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "正文保存失败" });
    } finally {
      this.finishOperation("save_draft_section", sectionId);
    }
  },

  async confirmChapter() {
    if (
      !this.data.canConfirmChapter
      || Boolean(this.data.editingSectionId)
    ) {
      return;
    }
    if (!this.beginOperation("confirm_chapter")) {
      return;
    }
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
      this.finishOperation("confirm_chapter");
    }
  },

  async performDraftSectionAIRevision(
    sectionId: string,
    instructionText: string,
  ) {
    const workspace = this.data.workspace;
    const normalizedInstruction = instructionText.trim();
    if (
      !workspace?.draft_version_id
      || !sectionId
      || !normalizedInstruction
      || !this.beginOperation("revise_draft_section_with_ai", sectionId)
    ) {
      return;
    }
    try {
      const revisedWorkspace = await reviseMemoirChapterDraftSectionWithAI(
        this.data.projectId,
        this.data.chapterId,
        sectionId,
        workspace.draft_version_id,
        normalizedInstruction,
      );
      if (
        revisedWorkspace.draft_version_id === workspace.draft_version_id
      ) {
        this.setData({
          error: "AI 没有改动这一小节，请把修改要求说得更具体一些",
        });
        return;
      }
      const expandedDraftSectionIds = {
        ...this.data.expandedDraftSectionIds,
        [sectionId]: true,
      };
      this.setData({
        expandedDraftSectionIds,
        ...CLOSED_DRAFT_SECTION_EDITOR,
      });
      await this.loadPageData(true);
      wx.showToast({
        title: "已按要求修改",
        icon: "success",
      });
      trackAuditAction(
        "chapter_workspace.section_ai_instruction_edit",
        {},
        { projectId: this.data.projectId },
      );
    } catch (error) {
      if (routeAuthError(error, "login")) {
        return;
      }
      this.setData({
        error: error instanceof Error
          ? error.message
          : "小节 AI 修改失败",
      });
    } finally {
      this.finishOperation("revise_draft_section_with_ai", sectionId);
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
      this.data.audioSectionId === sectionId
      && this.data.audioPhase !== "idle"
    ) {
      this.stopDraftSectionPlayback();
      return;
    }
    if (
      this.data.audioPhase !== "idle"
      || Boolean(this.data.operationKind)
      || Boolean(this.data.editingSectionId)
    ) {
      return;
    }
    this.stopDraftSectionPlayback();
    const token = this.audioPlayback.nextToken();
    this.playToken = token;
    this.setData({
      audioPhase: "preparing",
      audioSectionId: sectionId,
      error: "",
    });
    try {
      const response = await createMemoirBookChapterDraftSectionTTS(
        this.data.projectId,
        this.data.chapterId,
        sectionId,
        readBookTTSRequest(),
      );
      if (token !== this.playToken) {
        return;
      }
      this.setData({
        audioPhase: "playing",
        audioSectionId: sectionId,
      });
      await playAudioFileSequence(response.audio_file_ids || [], {
        token,
        isCurrent: (candidate) => candidate === this.playToken,
        playFile: (audioFileId) => this.audioPlayback.playFile(
          audioFileId,
          token,
          { errorMessage: "小节朗读失败" },
        ),
      });
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
          audioPhase: "idle",
          audioSectionId: "",
        });
      }
    }
  },

  stopDraftSectionPlayback() {
    this.audioPlayback.stop();
    this.playToken = this.audioPlayback.currentToken();
    if (this.data.audioPhase !== "idle" || this.data.audioSectionId) {
      this.setData({
        audioPhase: "idle",
        audioSectionId: "",
      });
    }
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
        error: error instanceof Error ? error.message : "本章重新生成失败",
      });
    }
  },

  schedulePreparationPolling() {
    if (
      !this.pageVisible
      || this.preparationPollTimer !== null
      || !isActiveChapterPreparation(this.data.chapterPreparationJob)
    ) {
      return;
    }
    this.preparationPollTimer = setTimeout(() => {
      this.preparationPollTimer = null;
      if (this.pageVisible) {
        void this.refreshChapterPreparationJob();
      }
    }, PREPARATION_POLL_INTERVAL_MS);
  },

  stopPreparationPolling() {
    if (this.preparationPollTimer !== null) {
      clearTimeout(this.preparationPollTimer);
      this.preparationPollTimer = null;
    }
  },

  scheduleSectionRegenerationPolling() {
    if (
      !this.pageVisible
      || this.sectionRegenerationPollTimer !== null
      || !isActiveSectionRegeneration(this.data.sectionRegenerationJob)
    ) {
      return;
    }
    this.sectionRegenerationPollTimer = setTimeout(() => {
      this.sectionRegenerationPollTimer = null;
      if (this.pageVisible) {
        void this.refreshSectionRegenerationJob();
      }
    }, SECTION_REGENERATION_POLL_INTERVAL_MS);
  },

  stopSectionRegenerationPolling() {
    if (this.sectionRegenerationPollTimer !== null) {
      clearTimeout(this.sectionRegenerationPollTimer);
      this.sectionRegenerationPollTimer = null;
    }
  },
});
