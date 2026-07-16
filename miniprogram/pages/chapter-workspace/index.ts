import type {
  MemoirChapterWorkspace,
  MemoirNarrativeGapAction,
} from "../../domain/chapter-workspace";
import type { MemoirDraftBlockJob } from "../../domain/draft-blocks";
import type { MemoirFollowupTask } from "../../domain/voice-chat";
import { trackAuditAction } from "../../services/audit-tracker";
import {
  clearMemoirDraftJobCursor,
  readMemoirDraftJobCursor,
  writeMemoirDraftJobCursor,
} from "../../services/memoir-draft-job-state";
import { definePage } from "../../services/page-definition";
import {
  applyMemoirDraftBlockRevision,
  assembleMemoirChapterDraftBlocks,
  confirmMemoirBookChapterDraft,
  confirmMemoirChapterContract,
  createMemoirVoiceSession,
  decideMemoirDraftBlock,
  enqueueMemoirChapterWorkspaceAudit,
  enqueueMemoirDraftBlockGeneration,
  enqueueMemoirDraftBlockRevision,
  getMemoirChapterWorkspace,
  getMemoirDraftBlockJob,
  listMemoirDraftBlocks,
  listMemoirFollowupTasks,
  manuallyReviseMemoirDraftBlock,
  resolveMemoirNarrativeGap,
  suggestMemoirChapterDirection,
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
  chapterNextAction,
  LOADING_NEXT_ACTION,
} from "./chapter-next-action";
import {
  chapterWorkspaceViewPatch,
  draftBlockViews,
  type ChapterWorkspaceViewPatch,
  type DraftBlockView,
} from "./chapter-workspace-view";

export { chapterNextAction, chapterWorkspaceViewPatch };

const AUDIT_POLL_INTERVAL_MS = 1500;
const DRAFT_JOB_POLL_INTERVAL_MS = 1500;
const GAP_ACTIONS = new Set(["resolve", "accept-incomplete", "not-applicable"]);
type DraftJobMode = "generate" | "revise";

type WxWithPageScrollTo = typeof wx & {
  pageScrollTo?: (options: { selector: string; duration?: number }) => void;
};

function scrollToSection(selector: string) {
  const pageScrollTo = (wx as WxWithPageScrollTo).pageScrollTo;
  if (typeof pageScrollTo === "function") {
    pageScrollTo({ selector, duration: 250 });
  }
}

type LoadResult<T> =
  | { value: T; error: null }
  | { value: null; error: unknown };

async function captureLoad<T>(promise: Promise<T>): Promise<LoadResult<T>> {
  try {
    return { value: await promise, error: null };
  } catch (error) {
    return { value: null, error };
  }
}

interface WorkspaceDataset {
  action?: string;
  blockId?: string;
  gapId?: string;
  taskId?: string;
}

type DatasetEvent = PageDatasetEvent<WorkspaceDataset>;

interface ChapterWorkspacePageData extends ChapterWorkspaceViewPatch {
  loading: boolean;
  error: string;
  projectId: string;
  chapterId: string;
  workspace: MemoirChapterWorkspace | null;
  followupTasks: MemoirFollowupTask[];
  followupError: string;
  draftBlocks: DraftBlockView[];
  draftError: string;
  auditBusy: boolean;
  auditError: string;
  contractBusy: boolean;
  startingFollowupTaskId: string;
  actionBusyGapId: string;
  draftGenerationBusy: boolean;
  draftGenerationJobId: string;
  draftGenerationStatus: string;
  assemblingDraft: boolean;
  editingBlockId: string;
  editingBlockContent: string;
  sourceBlockId: string;
  revisionBlockId: string;
  revisionJobId: string;
  revisionSuggestion: string;
  revisionStatus: string;
}

definePage<ChapterWorkspacePageData>()({
  auditPollTimer: null as ReturnType<typeof setTimeout> | null,
  auditPollToken: 0,
  draftPollTimer: null as ReturnType<typeof setTimeout> | null,
  draftPollToken: 0,
  loadedOnce: false,

  data: {
    loading: false,
    error: "",
    projectId: "",
    chapterId: "",
    workspace: null,
    followupTasks: [],
    followupError: "",
    draftBlocks: [],
    draftError: "",
    nextAction: LOADING_NEXT_ACTION,
    recordedCount: 0,
    mustConfirmCount: 0,
    editorialCount: 0,
    suggestionCount: 0,
    acceptedIncompleteCount: 0,
    readinessTitle: "正在整理本章",
    readinessDetail: "",
    recordedStories: [],
    directions: [],
    confirmations: [],
    reviewGaps: [],
    editorialGaps: [],
    suggestionHints: [],
    appliedAnswerUpdates: [],
    outlineTitles: [],
    canAssembleDraft: false,
    auditBusy: false,
    auditError: "",
    contractBusy: false,
    startingFollowupTaskId: "",
    actionBusyGapId: "",
    draftGenerationBusy: false,
    draftGenerationJobId: "",
    draftGenerationStatus: "",
    assemblingDraft: false,
    editingBlockId: "",
    editingBlockContent: "",
    sourceBlockId: "",
    revisionBlockId: "",
    revisionJobId: "",
    revisionSuggestion: "",
    revisionStatus: "",
  },

  async onLoad(query: PageQuery = {}) {
    const projectId = queryParam(query, "projectId");
    const chapterId = queryParam(query, "chapterId");
    this.setData({ projectId, chapterId, error: "" });
    if (!projectId || !chapterId) {
      this.setData({ error: "缺少章节信息" });
      return;
    }
    await this.loadPageData();
    await this.restoreDraftJob();
    this.loadedOnce = true;
  },

  async loadPageData(silent = false) {
    if (!this.data.projectId || !this.data.chapterId || (this.data.loading && !silent)) {
      return;
    }
    if (!silent) {
      this.setData({ loading: true, error: "" });
    }
    try {
      const workspace = await getMemoirChapterWorkspace(this.data.projectId, this.data.chapterId);
      const [taskResult, blockResult] = await Promise.all([
        captureLoad(listMemoirFollowupTasks(
          this.data.projectId,
          { chapterId: this.data.chapterId, limit: 100 },
        )),
        captureLoad(listMemoirDraftBlocks(this.data.projectId, this.data.chapterId)),
      ]);
      if (taskResult.error && routeAuthError(taskResult.error, "me")) {
        return;
      }
      if (blockResult.error && routeAuthError(blockResult.error, "me")) {
        return;
      }
      const followupTasks = taskResult.value?.items || this.data.followupTasks;
      const draftBlocks = draftBlockViews(
        blockResult.value?.blocks || this.data.draftBlocks,
        workspace,
        this.data.draftBlocks,
      );
      const auditBusy = workspace.latest_audit_job?.status === "pending"
        || workspace.latest_audit_job?.status === "running";
      const viewPatch = chapterWorkspaceViewPatch(workspace, followupTasks, draftBlocks);
      if (taskResult.error || blockResult.error) {
        viewPatch.nextAction = {
          kind: "wait",
          eyebrow: "部分内容没有读取完成",
          title: "下拉刷新后再决定下一步",
          detail: "本章概览仍然可以查看，但现在不能可靠判断最合适的下一步。",
          actionText: "",
          disabled: true,
        };
      }
      this.setData({
        workspace,
        followupTasks,
        draftBlocks,
        auditBusy,
        auditError: workspace.latest_audit_job?.status === "failed"
          ? (workspace.latest_audit_job.error_message || "本章整理失败")
          : "",
        error: "",
        followupError: taskResult.error
          ? (taskResult.error instanceof Error ? taskResult.error.message : "补充方向加载失败")
          : "",
        draftError: blockResult.error
          ? (blockResult.error instanceof Error ? blockResult.error.message : "本章正文加载失败")
          : "",
        ...viewPatch,
      });
      this.startAuditPollingIfNeeded();
    } catch (error) {
      if (routeAuthError(error, "me")) {
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "章节情况加载失败" });
    } finally {
      if (!silent) {
        this.setData({ loading: false });
      }
    }
  },

  async runNextAction() {
    const action = this.data.nextAction;
    if (action.disabled) {
      return;
    }
    if (action.kind === "create_direction") {
      await this.suggestCurrentDirection();
    } else if (action.kind === "confirm_direction") {
      await this.confirmCurrentDirection();
    } else if (action.kind === "start_audit") {
      await this.startAudit();
    } else if (action.kind === "continue_fact_confirmation" || action.kind === "open_followups") {
      await this.beginFollowupInterview(action.taskId || this.data.directions[0]?.id || "");
    } else if (action.kind === "open_checks") {
      scrollToSection("#confirmations");
    } else if (action.kind === "open_editorial_fix") {
      scrollToSection("#editorial-fixes");
    } else if (action.kind === "generate_draft") {
      await this.generateDraftBlocks();
    } else if (action.kind === "open_drafts") {
      scrollToSection("#chapter-draft");
    }
  },

  openEditorialFix() {
    scrollToSection("#chapter-draft");
  },

  async suggestCurrentDirection() {
    if (this.data.workspace?.contract || this.data.contractBusy) {
      return;
    }
    this.setData({ contractBusy: true, error: "" });
    try {
      await suggestMemoirChapterDirection(this.data.projectId, this.data.chapterId);
      await this.loadPageData(true);
    } catch (error) {
      if (routeAuthError(error, "me")) {
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "本章方向整理失败" });
    } finally {
      this.setData({ contractBusy: false });
    }
  },

  async confirmCurrentDirection() {
    const contract = this.data.workspace?.contract;
    if (!contract || contract.status === "confirmed" || this.data.contractBusy) {
      return;
    }
    this.setData({ contractBusy: true, error: "" });
    try {
      await confirmMemoirChapterContract(this.data.projectId, contract.id);
      await this.loadPageData(true);
      wx.showToast({ title: "本章方向已确认", icon: "success" });
    } catch (error) {
      if (routeAuthError(error, "me")) {
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "本章方向确认失败" });
    } finally {
      this.setData({ contractBusy: false });
    }
  },

  stopAuditPolling() {
    this.auditPollToken += 1;
    if (this.auditPollTimer !== null) {
      clearTimeout(this.auditPollTimer);
      this.auditPollTimer = null;
    }
  },

  startAuditPollingIfNeeded() {
    if (!this.data.auditBusy || this.auditPollTimer !== null) {
      return;
    }
    const token = this.auditPollToken;
    this.auditPollTimer = setTimeout(() => {
      this.auditPollTimer = null;
      void this.pollAudit(token);
    }, AUDIT_POLL_INTERVAL_MS);
  },

  async pollAudit(token: number) {
    if (token !== this.auditPollToken || !this.data.auditBusy) {
      return;
    }
    await this.loadPageData(true);
    if (token === this.auditPollToken && this.data.auditBusy) {
      this.startAuditPollingIfNeeded();
    }
  },

  async startAudit() {
    if (!this.data.projectId || !this.data.chapterId || this.data.auditBusy) {
      return;
    }
    this.stopAuditPolling();
    this.setData({ auditBusy: true, auditError: "" });
    try {
      await enqueueMemoirChapterWorkspaceAudit(this.data.projectId, this.data.chapterId);
      await this.loadPageData(true);
      this.startAuditPollingIfNeeded();
    } catch (error) {
      if (routeAuthError(error, "me")) {
        return;
      }
      this.setData({
        auditBusy: false,
        auditError: error instanceof Error ? error.message : "本章整理提交失败",
      });
    }
  },

  async startFollowupInterview(event: DatasetEvent) {
    await this.beginFollowupInterview(datasetString(event, "taskId"));
  },

  async beginFollowupInterview(taskId: string) {
    if (!taskId || this.data.startingFollowupTaskId) {
      return;
    }
    const task = this.data.followupTasks.find((item) => item.id === taskId);
    if (task?.status === "in_progress" && task.assigned_session_id) {
      this.openVoiceChat(task.assigned_session_id);
      return;
    }
    if (!task || (task.status !== "pending" && task.status !== "deferred")) {
      this.setData({ followupError: "这个补充方向已经处理，请下拉刷新" });
      return;
    }
    if (!readVoiceAuthorizationConsent()) {
      wx.navigateTo({ url: VOICE_AUTHORIZATION_PAGE_URL });
      return;
    }
    this.setData({ startingFollowupTaskId: taskId, followupError: "" });
    try {
      await syncVoiceAuthorizationConsent();
      const detail = await createMemoirVoiceSession({
        guidance_mode: "guided_interview",
        interview_type: "ai_interview",
        session_goal: "book_completion",
        current_followup_task_id: taskId,
        provider: "volc_streaming_asr",
        input_format: "pcm_s16le_16000_mono",
        metadata_json: {
          entry: "miniprogram_chapter_workspace",
          chapter_id: this.data.chapterId,
          followup_task_id: taskId,
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
      this.setData({ followupError: error instanceof Error ? error.message : "AI 访谈创建失败" });
    } finally {
      this.setData({ startingFollowupTaskId: "" });
    }
  },

  openVoiceChat(voiceSessionId: string) {
    wx.navigateTo({
      url: pageUrl("/pages/voice-chat/index", {
        voiceSessionId,
        mode: "guided_interview",
        interviewType: "ai_interview",
        sessionGoal: "book_completion",
        projectTitle: this.data.workspace?.chapter_title || "补全本章",
      }),
    });
  },

  async applyGapAction(event: DatasetEvent) {
    const gapId = datasetString(event, "gapId");
    const action = datasetString(event, "action") as MemoirNarrativeGapAction;
    if (!gapId || !GAP_ACTIONS.has(action) || this.data.actionBusyGapId) {
      return;
    }
    const gap = this.data.reviewGaps.find((item) => item.id === gapId);
    const note = gap?.resolutionText.trim() || "";
    if (action === "resolve" && !note) {
      this.setData({ auditError: "请先写明正确说法，再保存确认结果" });
      return;
    }
    this.setData({ actionBusyGapId: gapId, auditError: "" });
    try {
      await resolveMemoirNarrativeGap(this.data.projectId, gapId, action, {
        idempotency_key: `workspace:${gapId}:${action}:${Date.now()}`,
        note,
      });
      await this.loadPageData(true);
      wx.showToast({ title: "已保存", icon: "success" });
    } catch (error) {
      if (routeAuthError(error, "me")) {
        return;
      }
      this.setData({ auditError: error instanceof Error ? error.message : "确认结果保存失败" });
    } finally {
      this.setData({ actionBusyGapId: "" });
    }
  },

  changeGapResolution(event: PageTextInputEvent & DatasetEvent) {
    const gapId = datasetString(event, "gapId");
    if (!gapId) {
      return;
    }
    this.setData({
      reviewGaps: this.data.reviewGaps.map((gap) => (
        gap.id === gapId ? { ...gap, resolutionText: event.detail.value } : gap
      )),
    });
  },

  patchDraftBlock(blockId: string, patch: Partial<DraftBlockView>) {
    this.setData({
      draftBlocks: this.data.draftBlocks.map((block) => (
        block.id === blockId ? { ...block, ...patch } : block
      )),
    });
  },

  stopDraftPolling() {
    this.draftPollToken += 1;
    if (this.draftPollTimer !== null) {
      clearTimeout(this.draftPollTimer);
      this.draftPollTimer = null;
    }
  },

  scheduleDraftJobPoll(jobId: string, mode: DraftJobMode, blockId = "") {
    if (this.draftPollTimer !== null) {
      return;
    }
    const token = this.draftPollToken;
    this.draftPollTimer = setTimeout(() => {
      this.draftPollTimer = null;
      void this.pollDraftJob(jobId, mode, blockId, token);
    }, DRAFT_JOB_POLL_INTERVAL_MS);
  },

  rememberDraftJob(jobId: string, mode: DraftJobMode, blockId = "") {
    writeMemoirDraftJobCursor({
      projectId: this.data.projectId,
      chapterId: this.data.chapterId,
      jobId,
      mode,
      blockId,
    });
  },

  forgetDraftJob(jobId: string) {
    clearMemoirDraftJobCursor(this.data.projectId, this.data.chapterId, jobId);
  },

  async restoreDraftJob() {
    if (this.data.draftGenerationJobId || this.data.revisionJobId) {
      return;
    }
    const cursor = readMemoirDraftJobCursor(this.data.projectId, this.data.chapterId);
    if (!cursor) {
      return;
    }
    try {
      await this.applyDraftJobState(
        await getMemoirDraftBlockJob(this.data.projectId, cursor.jobId),
        cursor.mode,
        cursor.blockId,
      );
    } catch (error) {
      if (routeAuthError(error, "me")) {
        return;
      }
      this.setData({ draftError: error instanceof Error ? error.message : "正文任务状态恢复失败" });
    }
  },

  async applyDraftJobState(job: MemoirDraftBlockJob, mode: DraftJobMode, blockId = "") {
    if (job.status === "pending" || job.status === "running") {
      this.rememberDraftJob(job.id, mode, blockId);
      if (mode === "generate") {
        this.setData({
          draftGenerationBusy: true,
          draftGenerationJobId: job.id,
          draftGenerationStatus: job.status === "pending" ? "正文整理已排队" : "正在整理本章正文",
        });
      } else {
        this.setData({
          revisionBlockId: blockId,
          revisionJobId: job.id,
          revisionStatus: job.status === "pending" ? "修改意见已排队" : "正在改写这一段",
        });
      }
      this.scheduleDraftJobPoll(job.id, mode, blockId);
      return;
    }
    if (job.status === "failed" || job.status === "skipped") {
      this.forgetDraftJob(job.id);
      if (mode === "generate") {
        this.setData({
          draftGenerationBusy: false,
          draftGenerationJobId: "",
          draftGenerationStatus: "",
          draftError: job.error_message || "正文整理失败",
        });
      } else {
        this.setData({
          revisionBlockId: "",
          revisionJobId: "",
          revisionSuggestion: "",
          revisionStatus: "",
          draftError: job.error_message || "这一段修改失败",
        });
      }
      return;
    }
    if (mode === "revise") {
      const suggestion = typeof job.result_json.content === "string"
        ? job.result_json.content.trim()
        : "";
      if (!suggestion) {
        this.forgetDraftJob(job.id);
        this.setData({
          revisionBlockId: "",
          revisionJobId: "",
          revisionSuggestion: "",
          revisionStatus: "",
          draftError: "修改建议内容为空，请重新说明修改意见",
        });
        return;
      }
      this.rememberDraftJob(job.id, mode, blockId);
      this.setData({
        revisionBlockId: blockId,
        revisionJobId: job.id,
        revisionSuggestion: suggestion,
        revisionStatus: "建议版本已生成，采用后才会替换原文",
        draftError: "",
      });
      return;
    }
    this.forgetDraftJob(job.id);
    const created = Array.isArray(job.result_json.created_block_ids)
      ? job.result_json.created_block_ids.length
      : 0;
    const updated = Array.isArray(job.result_json.updated_block_ids)
      ? job.result_json.updated_block_ids.length
      : 0;
    const changed = created + updated;
    this.setData({
      draftGenerationBusy: false,
      draftGenerationJobId: "",
      draftGenerationStatus: changed ? `已整理 ${changed} 段正文` : "正文已经是最新内容",
      draftError: "",
    });
    await this.loadPageData(true);
  },

  async pollDraftJob(
    jobId: string,
    mode: DraftJobMode,
    blockId: string,
    token: number,
  ) {
    if (token !== this.draftPollToken) {
      return;
    }
    try {
      const job = await getMemoirDraftBlockJob(this.data.projectId, jobId);
      if (token === this.draftPollToken) {
        await this.applyDraftJobState(job, mode, blockId);
      }
    } catch (error) {
      if (routeAuthError(error, "me")) {
        return;
      }
      if (token === this.draftPollToken) {
        this.setData({ draftError: error instanceof Error ? error.message : "正文任务状态读取失败" });
        this.scheduleDraftJobPoll(jobId, mode, blockId);
      }
    }
  },

  async generateDraftBlocks() {
    if (
      !this.data.projectId
      || !this.data.chapterId
      || this.data.draftGenerationBusy
      || this.data.editingBlockId
      || this.data.revisionBlockId
    ) {
      return;
    }
    this.stopDraftPolling();
    this.setData({
      draftGenerationBusy: true,
      draftGenerationStatus: "正在提交正文整理",
      draftError: "",
    });
    try {
      await this.applyDraftJobState(
        await enqueueMemoirDraftBlockGeneration(this.data.projectId, this.data.chapterId),
        "generate",
      );
    } catch (error) {
      if (routeAuthError(error, "me")) {
        return;
      }
      this.setData({
        draftGenerationBusy: false,
        draftGenerationJobId: "",
        draftGenerationStatus: "",
        draftError: error instanceof Error ? error.message : "正文整理提交失败",
      });
    }
  },

  startDraftBlockEdit(event: DatasetEvent) {
    const blockId = datasetString(event, "blockId");
    const block = this.data.draftBlocks.find((item) => item.id === blockId);
    if (!block || block.busy || block.locked || this.data.revisionBlockId) {
      return;
    }
    this.setData({ editingBlockId: block.id, editingBlockContent: block.current_version.content });
  },

  toggleDraftBlockSources(event: DatasetEvent) {
    const blockId = datasetString(event, "blockId");
    this.setData({ sourceBlockId: this.data.sourceBlockId === blockId ? "" : blockId });
  },

  async confirmDraftBlock(event: DatasetEvent) {
    const blockId = datasetString(event, "blockId");
    const block = this.data.draftBlocks.find((item) => item.id === blockId);
    if (
      !block
      || block.busy
      || block.editorial_status === "confirmed"
      || block.freshness_status === "stale"
      || this.data.revisionBlockId
    ) {
      return;
    }
    this.patchDraftBlock(block.id, { busy: true });
    try {
      await decideMemoirDraftBlock(this.data.projectId, block.id, "confirm", {
        expected_current_version_id: block.current_version_id,
        idempotency_key: `workspace:confirm:${block.id}:${Date.now()}`,
        reason: "用户确认这一段正文可以用于成书",
      });
      await this.loadPageData(true);
      wx.showToast({ title: "这一段已确认", icon: "success" });
    } catch (error) {
      if (routeAuthError(error, "me")) {
        return;
      }
      this.setData({ draftError: error instanceof Error ? error.message : "正文确认失败" });
    } finally {
      this.patchDraftBlock(block.id, { busy: false });
    }
  },

  async toggleDraftBlockProtection(event: DatasetEvent) {
    const blockId = datasetString(event, "blockId");
    const block = this.data.draftBlocks.find((item) => item.id === blockId);
    if (!block || block.busy || this.data.revisionBlockId) {
      return;
    }
    const action = block.locked ? "unlock" : "lock";
    this.patchDraftBlock(block.id, { busy: true });
    try {
      await decideMemoirDraftBlock(this.data.projectId, block.id, action, {
        expected_current_version_id: block.current_version_id,
        idempotency_key: `workspace:${action}:${block.id}:${Date.now()}`,
        reason: block.locked ? "用户要继续修改这一段正文" : "用户保护认可的正文措辞",
      });
      await this.loadPageData(true);
      wx.showToast({ title: block.locked ? "现在可以修改" : "这一段已保护", icon: "success" });
    } catch (error) {
      if (routeAuthError(error, "me")) {
        return;
      }
      this.setData({ draftError: error instanceof Error ? error.message : "正文保护状态修改失败" });
    } finally {
      this.patchDraftBlock(block.id, { busy: false });
    }
  },

  changeDraftBlockContent(event: PageTextInputEvent) {
    this.setData({ editingBlockContent: event.detail.value });
  },

  cancelDraftBlockEdit() {
    this.setData({ editingBlockId: "", editingBlockContent: "" });
  },

  async saveDraftBlockEdit(event: DatasetEvent) {
    const blockId = datasetString(event, "blockId");
    const block = this.data.draftBlocks.find((item) => item.id === blockId);
    const content = this.data.editingBlockContent.trim();
    if (!block || block.busy || this.data.editingBlockId !== block.id || !content) {
      if (!content) {
        this.setData({ draftError: "正文内容不能为空" });
      }
      return;
    }
    this.patchDraftBlock(block.id, { busy: true });
    try {
      await manuallyReviseMemoirDraftBlock(this.data.projectId, block.id, {
        expected_current_version_id: block.current_version_id,
        content,
      });
      this.setData({ editingBlockId: "", editingBlockContent: "", draftError: "" });
      await this.loadPageData(true);
    } catch (error) {
      if (routeAuthError(error, "me")) {
        return;
      }
      this.setData({ draftError: error instanceof Error ? error.message : "正文保存失败" });
    } finally {
      this.patchDraftBlock(block.id, { busy: false });
    }
  },

  changeDraftRevisionFeedback(event: PageTextInputEvent & DatasetEvent) {
    const blockId = datasetString(event, "blockId");
    if (blockId) {
      this.patchDraftBlock(blockId, { revisionFeedback: event.detail.value });
    }
  },

  async requestDraftBlockRevision(event: DatasetEvent) {
    const blockId = datasetString(event, "blockId");
    const block = this.data.draftBlocks.find((item) => item.id === blockId);
    const feedback = block?.revisionFeedback.trim() || "";
    if (
      !block
      || block.locked
      || block.busy
      || this.data.revisionBlockId
      || this.data.draftGenerationBusy
      || this.data.editingBlockId
    ) {
      return;
    }
    if (!feedback) {
      this.setData({ draftError: "请先写明想怎样修改这一段" });
      return;
    }
    this.stopDraftPolling();
    this.setData({
      revisionBlockId: block.id,
      revisionJobId: "",
      revisionSuggestion: "",
      revisionStatus: "正在提交修改意见",
      draftError: "",
    });
    try {
      await this.applyDraftJobState(
        await enqueueMemoirDraftBlockRevision(this.data.projectId, block.id, {
          expected_current_version_id: block.current_version_id,
          feedback,
        }),
        "revise",
        block.id,
      );
    } catch (error) {
      if (routeAuthError(error, "me")) {
        return;
      }
      this.setData({
        revisionBlockId: "",
        revisionJobId: "",
        revisionStatus: "",
        draftError: error instanceof Error ? error.message : "修改意见提交失败",
      });
    }
  },

  discardDraftBlockRevision() {
    this.stopDraftPolling();
    this.forgetDraftJob(this.data.revisionJobId);
    this.setData({
      revisionBlockId: "",
      revisionJobId: "",
      revisionSuggestion: "",
      revisionStatus: "",
    });
  },

  async applyDraftBlockSuggestion(event: DatasetEvent) {
    const blockId = datasetString(event, "blockId");
    const block = this.data.draftBlocks.find((item) => item.id === blockId);
    if (
      !block
      || block.busy
      || this.data.revisionBlockId !== block.id
      || !this.data.revisionJobId
      || !this.data.revisionSuggestion
    ) {
      return;
    }
    this.patchDraftBlock(block.id, { busy: true });
    try {
      await applyMemoirDraftBlockRevision(this.data.projectId, block.id, {
        expected_current_version_id: block.current_version_id,
        generation_job_id: this.data.revisionJobId,
      });
      this.forgetDraftJob(this.data.revisionJobId);
      this.setData({
        revisionBlockId: "",
        revisionJobId: "",
        revisionSuggestion: "",
        revisionStatus: "",
        draftError: "",
      });
      this.patchDraftBlock(block.id, { revisionFeedback: "" });
      await this.loadPageData(true);
    } catch (error) {
      if (routeAuthError(error, "me")) {
        return;
      }
      this.setData({ draftError: error instanceof Error ? error.message : "建议版本采用失败" });
    } finally {
      this.patchDraftBlock(block.id, { busy: false });
    }
  },

  async assembleChapterDraft() {
    if (
      !this.data.projectId
      || !this.data.chapterId
      || this.data.assemblingDraft
      || this.data.editingBlockId
      || this.data.revisionBlockId
      || !this.data.canAssembleDraft
    ) {
      return;
    }
    this.setData({ assemblingDraft: true, draftError: "" });
    try {
      const result = await assembleMemoirChapterDraftBlocks(this.data.projectId, this.data.chapterId);
      await confirmMemoirBookChapterDraft(this.data.projectId, this.data.chapterId);
      this.setData({ draftGenerationStatus: `章节稿第 ${result.version} 版已确认` });
      wx.showToast({ title: "本章正文已确认", icon: "success" });
    } catch (error) {
      if (routeAuthError(error, "me")) {
        return;
      }
      this.setData({ draftError: error instanceof Error ? error.message : "章节稿确认失败" });
    } finally {
      this.setData({ assemblingDraft: false });
    }
  },

  async onPullDownRefresh() {
    await this.loadPageData(true);
    wx.stopPullDownRefresh();
  },

  onShow() {
    if (this.loadedOnce) {
      void this.loadPageData(true);
    }
    this.startAuditPollingIfNeeded();
    if (this.data.draftGenerationBusy && this.data.draftGenerationJobId) {
      this.scheduleDraftJobPoll(this.data.draftGenerationJobId, "generate");
    } else if (this.data.revisionBlockId && this.data.revisionJobId && !this.data.revisionSuggestion) {
      this.scheduleDraftJobPoll(this.data.revisionJobId, "revise", this.data.revisionBlockId);
    }
  },

  onHide() {
    this.stopAuditPolling();
    this.stopDraftPolling();
  },

  onUnload() {
    this.stopAuditPolling();
    this.stopDraftPolling();
  },

  goBack() {
    wx.navigateBack();
  },
});
