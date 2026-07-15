import type {
  MemoirChapterWorkspace,
  MemoirChapterWorkspaceStory,
  MemoirNarrativeGapAction,
} from "../../domain/chapter-workspace";
import type { MemoirDraftBlock, MemoirDraftBlockJob } from "../../domain/draft-blocks";
import type { MemoirFollowupTask } from "../../domain/voice-chat";
import { trackAuditAction } from "../../services/audit-tracker";
import {
  clearMemoirDraftJobCursor,
  readMemoirDraftJobCursor,
  writeMemoirDraftJobCursor,
} from "../../services/memoir-draft-job-state";
import { definePage } from "../../services/page-definition";
import {
  assembleMemoirChapterDraftBlocks,
  confirmMemoirChapterContract,
  createMemoirVoiceSession,
  enqueueMemoirChapterWorkspaceAudit,
  enqueueMemoirDraftBlockGeneration,
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
  type ChapterNextAction,
} from "./chapter-next-action";

export { chapterNextAction };

const AUDIT_POLL_INTERVAL_MS = 1500;
const DRAFT_JOB_POLL_INTERVAL_MS = 1500;
const ACTIVE_FOLLOWUP_STATUSES = new Set(["pending", "deferred", "in_progress"]);
const GAP_ACTIONS = new Set(["resolve", "accept-incomplete", "not-applicable"]);

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

export interface RecordedStoryView {
  id: string;
  title: string;
  summary: string;
}

export interface FollowupDirectionView {
  id: string;
  question: string;
  contextText: string;
  actionText: string;
  statusText: string;
}

export interface ConfirmationView {
  id: string;
  taskId: string;
  question: string;
  answerText: string;
}

export interface ReviewGapView {
  id: string;
  title: string;
  description: string;
  taskId: string;
}

export interface AnswerUpdateView {
  id: string;
  question: string;
  summaryText: string;
}

export interface DraftBlockView extends MemoirDraftBlock {
  busy: boolean;
  sectionTitle: string;
  statusText: string;
}

export interface ChapterWorkspaceViewPatch {
  nextAction: ChapterNextAction;
  recordedCount: number;
  mustConfirmCount: number;
  suggestionCount: number;
  acceptedIncompleteCount: number;
  readinessTitle: string;
  readinessDetail: string;
  recordedStories: RecordedStoryView[];
  directions: FollowupDirectionView[];
  confirmations: ConfirmationView[];
  reviewGaps: ReviewGapView[];
  suggestionHints: ReviewGapView[];
  appliedAnswerUpdates: AnswerUpdateView[];
  outlineTitles: string[];
}

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
}

function storyHasUsableEvidence(story: MemoirChapterWorkspaceStory): boolean {
  if (story.publication_status !== "eligible") {
    return false;
  }
  return story.claims.some((claim) => (
    claim.publication_status === "eligible"
    && claim.verification_status !== "rejected"
    && claim.evidence.some((evidence) => (
      evidence.evidence_role === "supports"
      && Boolean((evidence.quote || evidence.evidence_note || "").trim())
    ))
  ));
}

function activeFollowupTasks(tasks: MemoirFollowupTask[]): MemoirFollowupTask[] {
  return tasks
    .filter((task) => ACTIVE_FOLLOWUP_STATUSES.has(task.status))
    .sort((left, right) => right.priority - left.priority);
}

function directionView(task: MemoirFollowupTask): FollowupDirectionView {
  const inProgress = task.status === "in_progress";
  return {
    id: task.id,
    question: task.question,
    contextText: task.known_context_summary,
    actionText: inProgress ? "继续回答" : "讲这一段",
    statusText: inProgress ? "上次讲到这里" : "可以选择",
  };
}

function draftBlockView(block: MemoirDraftBlock, existing: DraftBlockView[]): DraftBlockView {
  const sectionTitle = ({
    opening: "开头",
    factual: "正文",
    transition: "衔接",
    reflection: "回望与感受",
    closing: "结尾",
    legacy_snapshot: "旧版正文",
  })[block.block_role];
  const statusText = block.freshness_status === "stale"
    ? "新内容还未写入"
    : (block.editorial_status === "draft" ? "请通读" : "已保存");
  return {
    ...block,
    busy: existing.find((item) => item.id === block.id)?.busy || false,
    sectionTitle,
    statusText,
  };
}

function draftBlockViews(blocks: MemoirDraftBlock[], existing: DraftBlockView[] = []): DraftBlockView[] {
  return blocks
    .filter((block) => block.block_role !== "legacy_snapshot")
    .map((block) => draftBlockView(block, existing));
}

export function chapterWorkspaceViewPatch(
  workspace: MemoirChapterWorkspace,
  followupTasks: MemoirFollowupTask[],
  draftBlocks: MemoirDraftBlock[],
): ChapterWorkspaceViewPatch {
  const recordedStories = workspace.stories
    .filter(storyHasUsableEvidence)
    .map((story) => ({ id: story.id, title: story.title, summary: story.summary }));
  const confirmations = workspace.answer_updates.filter((item) => item.status === "needs_confirmation");
  const confirmationTaskIds = new Set(confirmations.map((item) => item.task_id));
  const tasksById = new Map(followupTasks.map((task) => [task.id, task]));
  const confirmationGapIds = new Set(
    confirmations
      .map((item) => tasksById.get(item.task_id)?.gap_id)
      .filter((gapId): gapId is string => Boolean(gapId)),
  );
  const reviewGaps = workspace.gaps.must_handle.filter((gap) => !confirmationGapIds.has(gap.id));
  const mustHandleGapIds = new Set(workspace.gaps.must_handle.map((gap) => gap.id));
  const activeTasks = activeFollowupTasks(followupTasks).filter((task) => (
    !confirmationTaskIds.has(task.id) && !mustHandleGapIds.has(task.gap_id)
  ));
  const currentDraftBlocks = draftBlocks.filter((block) => block.block_role !== "legacy_snapshot");
  const mustConfirmCount = confirmations.length + reviewGaps.length;
  const suggestionCount = Math.max(activeTasks.length, workspace.gaps.suggested.length);
  const hasStaleDraft = currentDraftBlocks.some((block) => block.freshness_status === "stale");

  let readinessTitle = "现有内容已经可以开始写正文";
  let readinessDetail = "您也可以先选择下面任意一个方向继续补充。";
  if (!workspace.contract) {
    readinessTitle = "先确定这章主要写什么";
    readinessDetail = "整理方向后，您会先看到整章还缺什么，再决定从哪里开始。";
  } else if (mustConfirmCount) {
    readinessTitle = `有 ${mustConfirmCount} 件事需要先确认`;
    readinessDetail = "这些内容会影响人物、时间或事实是否准确，确认后再进入正文。";
  } else if (workspace.latest_audit_job?.status === "pending" || workspace.latest_audit_job?.status === "running") {
    readinessTitle = "正在整理本章的完整情况";
    readinessDetail = "整理完成后会自动显示可选择的补充方向。";
  } else if (hasStaleDraft) {
    readinessTitle = "新回答已收到，正文需要重新整理";
    readinessDetail = "重新整理只更新受影响的段落，您手动保护的内容不会被覆盖。";
  } else if (currentDraftBlocks.length) {
    readinessTitle = "现有内容可以形成章节正文";
    readinessDetail = suggestionCount
      ? "正文已经可读；下面的内容属于可选补充，不会阻止成书。"
      : "目前没有必须补充的内容，可以直接通读本章正文。";
  }

  return {
    nextAction: chapterNextAction(workspace, followupTasks, currentDraftBlocks),
    recordedCount: recordedStories.length,
    mustConfirmCount,
    suggestionCount,
    acceptedIncompleteCount: workspace.gaps.accepted_incomplete.length,
    readinessTitle,
    readinessDetail,
    recordedStories,
    directions: activeTasks.slice(0, 3).map(directionView),
    confirmations: confirmations.map((item) => ({
      id: item.id,
      taskId: item.task_id,
      question: item.question,
      answerText: item.answer_text,
    })),
    reviewGaps: reviewGaps.map((gap) => ({
      id: gap.id,
      title: gap.title,
      description: gap.description,
      taskId: activeFollowupTasks(followupTasks).find((task) => task.gap_id === gap.id)?.id || "",
    })),
    suggestionHints: workspace.gaps.suggested.slice(0, 3).map((gap) => ({
      id: gap.id,
      title: gap.title,
      description: gap.description,
      taskId: "",
    })),
    appliedAnswerUpdates: workspace.answer_updates
      .filter((item) => item.status === "applied")
      .slice(0, 3)
      .map((item) => ({
        id: item.id,
        question: item.question,
        summaryText: item.summaries.join("\n"),
      })),
    outlineTitles: (workspace.contract?.nodes || []).map((node) => node.title),
  };
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
    suggestionCount: 0,
    acceptedIncompleteCount: 0,
    readinessTitle: "正在整理本章",
    readinessDetail: "",
    recordedStories: [],
    directions: [],
    confirmations: [],
    reviewGaps: [],
    suggestionHints: [],
    appliedAnswerUpdates: [],
    outlineTitles: [],
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
    } else if (action.kind === "generate_draft") {
      await this.generateDraftBlocks();
    } else if (action.kind === "open_drafts") {
      scrollToSection("#chapter-draft");
    }
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
    this.setData({ actionBusyGapId: gapId, auditError: "" });
    try {
      await resolveMemoirNarrativeGap(this.data.projectId, gapId, action, {
        idempotency_key: `workspace:${gapId}:${action}:${Date.now()}`,
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

  scheduleDraftJobPoll(jobId: string) {
    if (this.draftPollTimer !== null) {
      return;
    }
    const token = this.draftPollToken;
    this.draftPollTimer = setTimeout(() => {
      this.draftPollTimer = null;
      void this.pollDraftJob(jobId, token);
    }, DRAFT_JOB_POLL_INTERVAL_MS);
  },

  rememberDraftJob(jobId: string) {
    writeMemoirDraftJobCursor({
      projectId: this.data.projectId,
      chapterId: this.data.chapterId,
      jobId,
      mode: "generate",
      blockId: "",
    });
  },

  forgetDraftJob(jobId: string) {
    clearMemoirDraftJobCursor(this.data.projectId, this.data.chapterId, jobId);
  },

  async restoreDraftJob() {
    if (this.data.draftGenerationJobId) {
      return;
    }
    const cursor = readMemoirDraftJobCursor(this.data.projectId, this.data.chapterId);
    if (!cursor || cursor.mode !== "generate") {
      return;
    }
    try {
      await this.applyDraftJobState(await getMemoirDraftBlockJob(this.data.projectId, cursor.jobId));
    } catch (error) {
      if (routeAuthError(error, "me")) {
        return;
      }
      this.setData({ draftError: error instanceof Error ? error.message : "正文任务状态恢复失败" });
    }
  },

  async applyDraftJobState(job: MemoirDraftBlockJob) {
    if (job.status === "pending" || job.status === "running") {
      this.rememberDraftJob(job.id);
      this.setData({
        draftGenerationBusy: true,
        draftGenerationJobId: job.id,
        draftGenerationStatus: job.status === "pending" ? "正文整理已排队" : "正在整理本章正文",
      });
      this.scheduleDraftJobPoll(job.id);
      return;
    }
    this.forgetDraftJob(job.id);
    if (job.status === "failed" || job.status === "skipped") {
      this.setData({
        draftGenerationBusy: false,
        draftGenerationJobId: "",
        draftGenerationStatus: "",
        draftError: job.error_message || "正文整理失败",
      });
      return;
    }
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

  async pollDraftJob(jobId: string, token: number) {
    if (token !== this.draftPollToken) {
      return;
    }
    try {
      const job = await getMemoirDraftBlockJob(this.data.projectId, jobId);
      if (token === this.draftPollToken) {
        await this.applyDraftJobState(job);
      }
    } catch (error) {
      if (routeAuthError(error, "me")) {
        return;
      }
      if (token === this.draftPollToken) {
        this.setData({ draftError: error instanceof Error ? error.message : "正文任务状态读取失败" });
        this.scheduleDraftJobPoll(jobId);
      }
    }
  },

  async generateDraftBlocks() {
    if (!this.data.projectId || !this.data.chapterId || this.data.draftGenerationBusy) {
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
    if (!block || block.busy) {
      return;
    }
    this.setData({ editingBlockId: block.id, editingBlockContent: block.current_version.content });
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

  async assembleChapterDraft() {
    if (!this.data.projectId || !this.data.chapterId || this.data.assemblingDraft) {
      return;
    }
    this.setData({ assemblingDraft: true, draftError: "" });
    try {
      const result = await assembleMemoirChapterDraftBlocks(this.data.projectId, this.data.chapterId);
      this.setData({ draftGenerationStatus: `章节稿第 ${result.version} 版已保存` });
      wx.showToast({ title: "章节稿已保存", icon: "success" });
    } catch (error) {
      if (routeAuthError(error, "me")) {
        return;
      }
      this.setData({ draftError: error instanceof Error ? error.message : "章节稿保存失败" });
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
      this.scheduleDraftJobPoll(this.data.draftGenerationJobId);
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
