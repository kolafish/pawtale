import type {
  MemoirChapterWorkspace,
  MemoirChapterWorkspaceClaim,
  MemoirChapterWorkspaceNode,
  MemoirChapterWorkspaceStory,
  MemoirChapterWorkspaceStorySlot,
  MemoirNarrativeGap,
  MemoirNarrativeGapAction,
} from "../../domain/chapter-workspace";
import type { MemoirFollowupTask } from "../../domain/voice-chat";
import type {
  MemoirDraftBlock,
  MemoirDraftBlockJob,
} from "../../domain/draft-blocks";
import { trackAuditAction } from "../../services/audit-tracker";
import {
  clearMemoirDraftJobCursor,
  readMemoirDraftJobCursor,
  writeMemoirDraftJobCursor,
} from "../../services/memoir-draft-job-state";
import { definePage } from "../../services/page-definition";
import {
  createMemoirVoiceSession,
  applyMemoirDraftBlockRevision,
  assembleMemoirChapterDraftBlocks,
  decideMemoirDraftBlock,
  enqueueMemoirDraftBlockGeneration,
  enqueueMemoirDraftBlockRevision,
  enqueueMemoirChapterWorkspaceAudit,
  getMemoirDraftBlockJob,
  getMemoirChapterWorkspace,
  listMemoirDraftBlocks,
  listMemoirFollowupTasks,
  manuallyReviseMemoirDraftBlock,
  resolveMemoirNarrativeGap,
} from "../../services/memoir-voice-api";
import { routeAuthError } from "../../services/navigation";
import {
  datasetString,
  pageUrl,
  queryChoice,
  queryParam,
  type PageDatasetEvent,
  type PageQuery,
  type PageTextInputEvent,
} from "../../services/page-input";


const TAB_VALUES = ["structure", "stories", "drafts", "checks", "followups"] as const;
const GAP_ACTIONS = ["resolve", "accept-incomplete", "not-applicable", "dismiss"] as const;
const AUDIT_POLL_INTERVAL_MS = 1500;
const DRAFT_JOB_POLL_INTERVAL_MS = 1500;

type WorkspaceTab = (typeof TAB_VALUES)[number];

interface WorkspaceDataset {
  tab?: string;
  gapId?: string;
  action?: string;
  taskId?: string;
  blockId?: string;
}

type DatasetEvent = PageDatasetEvent<WorkspaceDataset>;

interface NodeView extends MemoirChapterWorkspaceNode {
  statusText: string;
  storyText: string;
}

interface StorySlotView extends MemoirChapterWorkspaceStorySlot {
  labelText: string;
  statusText: string;
  statusClass: string;
}

interface ClaimView extends MemoirChapterWorkspaceClaim {
  evidenceCountText: string;
}

interface StoryView extends MemoirChapterWorkspaceStory {
  statusText: string;
  slots: StorySlotView[];
  claims: ClaimView[];
}

interface GapView extends MemoirNarrativeGap {
  severityText: string;
  statusText: string;
  note: string;
}

interface GapGroupView {
  key: string;
  title: string;
  emptyText: string;
  actionable: boolean;
  items: GapView[];
}

interface FollowupTaskView extends MemoirFollowupTask {
  statusText: string;
  priorityText: string;
  canInterview: boolean;
  actionText: string;
}

interface DraftBlockSourceClaimView {
  id: string;
  statement: string;
  evidenceText: string;
}

interface DraftBlockSourceStoryView {
  id: string;
  title: string;
}

interface DraftBlockView extends MemoirDraftBlock {
  roleText: string;
  nodeTitle: string;
  editorialStatusText: string;
  freshnessStatusText: string;
  sourceCountText: string;
  sourceClaims: DraftBlockSourceClaimView[];
  sourceStories: DraftBlockSourceStoryView[];
  revisionFeedback: string;
  isLegacy: boolean;
  busy: boolean;
}

interface ChapterWorkspacePageData {
  loading: boolean;
  error: string;
  projectId: string;
  chapterId: string;
  activeTab: WorkspaceTab;
  workspace: MemoirChapterWorkspace | null;
  contractStatusText: string;
  auditStatusText: string;
  auditError: string;
  auditBusy: boolean;
  actionBusyGapId: string;
  nodes: NodeView[];
  stories: StoryView[];
  gapGroups: GapGroupView[];
  followupTasks: FollowupTaskView[];
  followupLoading: boolean;
  followupError: string;
  startingFollowupTaskId: string;
  draftBlocks: DraftBlockView[];
  draftLoading: boolean;
  draftError: string;
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

const SLOT_LABELS: Record<string, string> = {
  background: "背景",
  trigger: "起因",
  people: "人物",
  difficulty: "困难",
  actions: "行动",
  turning_point: "转折",
  outcome: "结果",
  reflection: "感受",
  meeting: "相识",
  person_portrait: "人物印象",
  shared_experience: "共同经历",
  relationship_change: "关系变化",
  representative_scene: "代表场景",
  entry_reason: "进入原因",
  responsibilities: "承担的责任",
  representative_events: "代表事件",
  ability_and_character: "能力与性格",
  transition: "阶段变化",
  theme_definition: "主题含义",
  supporting_cases: "支撑故事",
  contrast_or_failure: "对照或挫折",
  continuity: "延续与影响",
};

function nodeStatusText(status: string): string {
  return ({ planned: "待规划", ready: "已准备", needs_work: "需补充", excluded: "已排除" })[status] || status;
}

function slotStatus(status: string): { text: string; className: string } {
  const values: Record<string, { text: string; className: string }> = {
    missing: { text: "缺少", className: "missing" },
    partial: { text: "部分具备", className: "partial" },
    sufficient: { text: "已有依据", className: "sufficient" },
    accepted_incomplete: { text: "接受不完整", className: "accepted" },
    not_applicable: { text: "不适用", className: "accepted" },
  };
  return values[status] || { text: status, className: "partial" };
}

function storyStatusText(story: MemoirChapterWorkspaceStory): string {
  if (story.publication_status !== "eligible") {
    return "不用于成书";
  }
  if (story.verification_status === "rejected") {
    return "已否定";
  }
  return ({ incomplete: "待补充", sufficient: "内容充分", accepted_incomplete: "接受不完整" })[story.completion_status]
    || story.completion_status;
}

function gapStatusText(status: string): string {
  return ({
    open: "待处理",
    resolved: "已处理",
    accepted_incomplete: "接受不完整",
    not_applicable: "不适用",
    dismissed: "已忽略",
  })[status] || status;
}

function gapView(gap: MemoirNarrativeGap, note = ""): GapView {
  return {
    ...gap,
    severityText: ({ high: "重要", medium: "建议", low: "提示" })[gap.severity] || gap.severity,
    statusText: gapStatusText(gap.status),
    note,
  };
}

function auditState(workspace: MemoirChapterWorkspace): {
  text: string;
  error: string;
  busy: boolean;
} {
  const job = workspace.latest_audit_job;
  if (!job) {
    return { text: "尚未检查", error: "", busy: false };
  }
  if (job.status === "pending") {
    return { text: "检查已排队", error: "", busy: true };
  }
  if (job.status === "running") {
    return { text: "正在检查", error: "", busy: true };
  }
  if (job.status === "failed") {
    return { text: "检查失败", error: job.error_message || "章节检查失败", busy: false };
  }
  if (job.status === "skipped") {
    return { text: "检查未执行", error: job.error_message || "", busy: false };
  }
  return { text: "检查已更新", error: "", busy: false };
}

function isGapAction(value: string): value is MemoirNarrativeGapAction {
  return GAP_ACTIONS.includes(value as MemoirNarrativeGapAction);
}

function followupTaskStatusText(status: MemoirFollowupTask["status"]): string {
  return ({
    pending: "待访谈",
    in_progress: "访谈中",
    answered: "已补充",
    deferred: "以后再补",
    cannot_recall: "暂时想不起",
    excluded: "不收录",
    cancelled: "已取消",
  })[status];
}

export function followupTaskView(task: MemoirFollowupTask): FollowupTaskView {
  const canInterview = task.status === "pending" || task.status === "deferred";
  return {
    ...task,
    statusText: followupTaskStatusText(task.status),
    priorityText: task.priority >= 3000 ? "优先补充" : (task.priority >= 2000 ? "建议补充" : "有空再补"),
    canInterview,
    actionText: task.status === "deferred" ? "继续采访这题" : "采访这题",
  };
}

function draftRoleText(role: MemoirDraftBlock["block_role"]): string {
  return ({
    factual: "叙事正文",
    transition: "段落过渡",
    reflection: "回望与感受",
    opening: "章节开头",
    closing: "章节结尾",
    legacy_snapshot: "历史整章快照",
  })[role];
}

function firstEvidenceText(claim: MemoirChapterWorkspaceClaim): string {
  const evidence = claim.evidence.find((item) => item.evidence_role === "supports");
  return (evidence?.quote || evidence?.evidence_note || "").trim();
}

export function draftBlockViews(
  blocks: MemoirDraftBlock[],
  workspace: MemoirChapterWorkspace | null,
  existing: DraftBlockView[] = [],
): DraftBlockView[] {
  const nodeTitles = new Map(
    (workspace?.contract?.nodes || []).map((node) => [node.id, node.title]),
  );
  const stories = new Map((workspace?.stories || []).map((story) => [story.id, story]));
  const claims = new Map(
    (workspace?.stories || []).flatMap((story) => story.claims.map((claim) => [claim.id, claim] as const)),
  );
  const feedback = new Map(existing.map((block) => [block.id, block.revisionFeedback]));
  return blocks.map((block) => {
    const sourceClaims = block.current_version.claims.flatMap((source) => {
      const claim = claims.get(source.claim_id);
      return claim ? [{
        id: claim.id,
        statement: claim.statement,
        evidenceText: firstEvidenceText(claim),
      }] : [];
    });
    const sourceStories = block.current_version.stories.flatMap((source) => {
      const story = stories.get(source.story_id);
      return story ? [{ id: story.id, title: story.title }] : [];
    });
    const sourceCount = sourceClaims.length + sourceStories.length;
    return {
      ...block,
      roleText: draftRoleText(block.block_role),
      nodeTitle: (block.node_id && nodeTitles.get(block.node_id)) || "历史正文",
      editorialStatusText: ({ draft: "待确认", confirmed: "已确认", excluded: "不收录" })[block.editorial_status],
      freshnessStatusText: block.freshness_status === "stale" ? "来源已变化" : "来源有效",
      sourceCountText: sourceCount ? `${sourceCount} 项来源` : "无结构化来源",
      sourceClaims,
      sourceStories,
      revisionFeedback: feedback.get(block.id) || "",
      isLegacy: block.block_role === "legacy_snapshot",
      busy: existing.find((item) => item.id === block.id)?.busy || false,
    };
  });
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
    activeTab: "structure",
    workspace: null,
    contractStatusText: "尚未建立章节契约",
    auditStatusText: "尚未检查",
    auditError: "",
    auditBusy: false,
    actionBusyGapId: "",
    nodes: [],
    stories: [],
    gapGroups: [],
    followupTasks: [],
    followupLoading: false,
    followupError: "",
    startingFollowupTaskId: "",
    draftBlocks: [],
    draftLoading: false,
    draftError: "",
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
    const activeTab = queryChoice(query, "tab", TAB_VALUES, "structure");
    this.setData({ projectId, chapterId, activeTab, error: "" });
    if (!projectId || !chapterId) {
      this.setData({ error: "缺少章节信息" });
      return;
    }
    await this.loadWorkspace();
    await this.restoreDraftJob();
    this.loadedOnce = true;
  },

  applyWorkspace(workspace: MemoirChapterWorkspace) {
    const storyTitles = new Map(workspace.stories.map((story) => [story.id, story.title]));
    const notes = new Map(
      this.data.gapGroups.flatMap((group) => group.items.map((gap) => [gap.id, gap.note] as const)),
    );
    const state = auditState(workspace);
    const openGap = (gap: MemoirNarrativeGap) => gapView(gap, notes.get(gap.id) || "");
    this.setData({
      workspace,
      contractStatusText: workspace.contract
        ? `契约 v${workspace.contract.version} · ${workspace.contract.status === "confirmed" ? "已确认" : "草稿"}`
        : "尚未建立章节契约",
      auditStatusText: state.text,
      auditError: state.error,
      auditBusy: state.busy,
      nodes: (workspace.contract?.nodes || []).map((node) => ({
        ...node,
        statusText: nodeStatusText(node.status),
        storyText: node.stories.length
          ? node.stories.map((link) => storyTitles.get(link.story_id) || "未知故事").join("、")
          : "尚未关联故事",
      })),
      stories: workspace.stories.map((story) => ({
        ...story,
        statusText: storyStatusText(story),
        slots: story.slots.map((slot) => {
          const status = slotStatus(slot.status);
          return {
            ...slot,
            labelText: SLOT_LABELS[slot.slot_key] || slot.slot_key,
            statusText: status.text,
            statusClass: status.className,
          };
        }),
        claims: story.claims.map((claim) => ({
          ...claim,
          evidenceCountText: `${claim.evidence.length} 条证据`,
        })),
      })),
      gapGroups: [
        {
          key: "must_handle",
          title: "必须处理",
          emptyText: "没有必须处理的问题",
          actionable: true,
          items: workspace.gaps.must_handle.map(openGap),
        },
        {
          key: "suggested",
          title: "建议补充",
          emptyText: "没有建议补充的问题",
          actionable: true,
          items: workspace.gaps.suggested.map(openGap),
        },
        {
          key: "accepted_incomplete",
          title: "可以接受的不完整",
          emptyText: "尚未接受任何不完整项",
          actionable: false,
          items: workspace.gaps.accepted_incomplete.map(openGap),
        },
      ],
      draftBlocks: draftBlockViews(this.data.draftBlocks, workspace, this.data.draftBlocks),
    });
    this.startAuditPollingIfNeeded();
  },

  async loadWorkspace(silent = false) {
    if (!this.data.projectId || !this.data.chapterId || (this.data.loading && !silent)) {
      return;
    }
    if (!silent) {
      this.setData({ loading: true, error: "" });
    }
    try {
      const workspace = await getMemoirChapterWorkspace(this.data.projectId, this.data.chapterId);
      this.applyWorkspace(workspace);
      await Promise.all([
        this.loadFollowupTasks(true),
        this.loadDraftBlocks(true),
      ]);
    } catch (error) {
      if (routeAuthError(error, "me")) {
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "章节工作台加载失败" });
    } finally {
      if (!silent) {
        this.setData({ loading: false });
      }
    }
  },

  async loadFollowupTasks(silent = false) {
    if (!this.data.projectId || !this.data.chapterId || (this.data.followupLoading && !silent)) {
      return;
    }
    if (!silent) {
      this.setData({ followupLoading: true, followupError: "" });
    }
    try {
      const response = await listMemoirFollowupTasks(this.data.projectId, {
        chapterId: this.data.chapterId,
        limit: 100,
      });
      this.setData({
        followupTasks: response.items.map(followupTaskView),
        followupError: "",
      });
    } catch (error) {
      if (routeAuthError(error, "me")) {
        return;
      }
      this.setData({ followupError: error instanceof Error ? error.message : "补访问题加载失败" });
    } finally {
      if (!silent) {
        this.setData({ followupLoading: false });
      }
    }
  },

  async loadDraftBlocks(silent = false) {
    if (!this.data.projectId || !this.data.chapterId || (this.data.draftLoading && !silent)) {
      return;
    }
    if (!silent) {
      this.setData({ draftLoading: true, draftError: "" });
    }
    try {
      const response = await listMemoirDraftBlocks(this.data.projectId, this.data.chapterId);
      this.setData({
        draftBlocks: draftBlockViews(response.blocks, this.data.workspace, this.data.draftBlocks),
        draftError: "",
      });
    } catch (error) {
      if (routeAuthError(error, "me")) {
        return;
      }
      this.setData({ draftError: error instanceof Error ? error.message : "正文块加载失败" });
    } finally {
      if (!silent) {
        this.setData({ draftLoading: false });
      }
    }
  },

  changeTab(event: DatasetEvent) {
    this.setData({ activeTab: queryChoice({ tab: datasetString(event, "tab") }, "tab", TAB_VALUES, "structure") });
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
    await this.loadWorkspace(true);
    if (token === this.auditPollToken && this.data.auditBusy) {
      this.startAuditPollingIfNeeded();
    }
  },

  async startAudit() {
    if (!this.data.projectId || !this.data.chapterId || this.data.auditBusy) {
      return;
    }
    this.stopAuditPolling();
    this.setData({ auditBusy: true, auditStatusText: "正在提交检查", auditError: "" });
    try {
      await enqueueMemoirChapterWorkspaceAudit(this.data.projectId, this.data.chapterId);
      await this.loadWorkspace(true);
      this.startAuditPollingIfNeeded();
    } catch (error) {
      if (routeAuthError(error, "me")) {
        return;
      }
      this.setData({
        auditBusy: false,
        auditStatusText: "检查未提交",
        auditError: error instanceof Error ? error.message : "章节检查提交失败",
      });
    }
  },

  changeGapNote(event: PageTextInputEvent & DatasetEvent) {
    const gapId = datasetString(event, "gapId");
    if (!gapId) {
      return;
    }
    this.setData({
      gapGroups: this.data.gapGroups.map((group) => ({
        ...group,
        items: group.items.map((gap) => (
          gap.id === gapId ? { ...gap, note: event.detail.value } : gap
        )),
      })),
    });
  },

  async applyGapAction(event: DatasetEvent) {
    const gapId = datasetString(event, "gapId");
    const action = datasetString(event, "action");
    if (!gapId || !isGapAction(action) || this.data.actionBusyGapId === gapId) {
      return;
    }
    const gap = this.data.gapGroups.flatMap((group) => group.items).find((item) => item.id === gapId);
    if (!gap) {
      return;
    }
    const note = gap.note.trim();
    if (action === "dismiss" && !note) {
      this.setData({ auditError: "忽略问题时需要填写原因" });
      return;
    }
    this.setData({ actionBusyGapId: gapId, auditError: "" });
    try {
      await resolveMemoirNarrativeGap(this.data.projectId, gapId, action, {
        idempotency_key: `workspace:${gapId}:${action}:${Date.now()}`,
        note,
      });
      await this.loadWorkspace(true);
      wx.showToast({ title: "处理状态已保存", icon: "success" });
    } catch (error) {
      if (routeAuthError(error, "me")) {
        return;
      }
      this.setData({ auditError: error instanceof Error ? error.message : "问题处理失败" });
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

  scheduleDraftJobPoll(jobId: string, mode: "generate" | "revise", blockId = "") {
    if (this.draftPollTimer !== null) {
      return;
    }
    const token = this.draftPollToken;
    this.draftPollTimer = setTimeout(() => {
      this.draftPollTimer = null;
      void this.pollDraftJob(jobId, mode, blockId, token);
    }, DRAFT_JOB_POLL_INTERVAL_MS);
  },

  rememberDraftJob(jobId: string, mode: "generate" | "revise", blockId = "") {
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
      const job = await getMemoirDraftBlockJob(this.data.projectId, cursor.jobId);
      await this.applyDraftJobState(job, cursor.mode, cursor.blockId);
    } catch (error) {
      if (routeAuthError(error, "me")) {
        return;
      }
      this.setData({
        draftError: error instanceof Error ? error.message : "正文任务状态恢复失败",
      });
    }
  },

  async applyDraftJobState(job: MemoirDraftBlockJob, mode: "generate" | "revise", blockId = "") {
    if (job.status === "pending" || job.status === "running") {
      this.rememberDraftJob(job.id, mode, blockId);
      if (mode === "generate") {
        this.setData({
          draftGenerationBusy: true,
          draftGenerationJobId: job.id,
          draftGenerationStatus: job.status === "pending" ? "正文生成已排队" : "正在按章节结构生成正文",
        });
      } else {
        this.setData({
          revisionBlockId: blockId,
          revisionJobId: job.id,
          revisionStatus: job.status === "pending" ? "修改建议已排队" : "正在生成当前段的修改建议",
        });
      }
      this.scheduleDraftJobPoll(job.id, mode, blockId);
      return;
    }
    if (job.status === "failed" || job.status === "skipped") {
      this.forgetDraftJob(job.id);
      const message = job.error_message || (mode === "generate" ? "正文块生成失败" : "修改建议生成失败");
      if (mode === "generate") {
        this.setData({ draftGenerationBusy: false, draftGenerationJobId: "", draftGenerationStatus: "", draftError: message });
      } else {
        this.setData({ revisionBlockId: "", revisionJobId: "", revisionStatus: "", draftError: message });
      }
      return;
    }
    if (mode === "generate") {
      this.forgetDraftJob(job.id);
      const created = job.result_json.created_block_ids;
      const count = Array.isArray(created) ? created.length : 0;
      this.setData({
        draftGenerationBusy: false,
        draftGenerationJobId: "",
        draftGenerationStatus: count ? `已生成 ${count} 个正文块` : "现有正文块已保留",
        draftError: "",
      });
      await this.loadDraftBlocks(true);
      return;
    }
    const suggestion = typeof job.result_json.content === "string" ? job.result_json.content.trim() : "";
    this.rememberDraftJob(job.id, mode, blockId);
    this.setData({
      revisionBlockId: blockId,
      revisionJobId: job.id,
      revisionSuggestion: suggestion,
      revisionStatus: suggestion ? "修改建议已生成，确认后才会替换当前版本" : "修改建议没有返回正文",
      draftError: suggestion ? "" : "修改建议内容为空",
    });
  },

  async pollDraftJob(
    jobId: string,
    mode: "generate" | "revise",
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
        this.setData({
          draftError: error instanceof Error ? error.message : "正文任务状态读取失败",
        });
        this.scheduleDraftJobPoll(jobId, mode, blockId);
      }
    }
  },

  async generateDraftBlocks() {
    if (!this.data.projectId || !this.data.chapterId || this.data.draftGenerationBusy || this.data.revisionBlockId) {
      return;
    }
    this.stopDraftPolling();
    this.setData({
      activeTab: "drafts",
      draftGenerationBusy: true,
      draftGenerationStatus: "正在提交正文生成",
      draftError: "",
    });
    try {
      const job = await enqueueMemoirDraftBlockGeneration(this.data.projectId, this.data.chapterId);
      await this.applyDraftJobState(job, "generate");
    } catch (error) {
      if (routeAuthError(error, "me")) {
        return;
      }
      this.setData({
        draftGenerationBusy: false,
        draftGenerationJobId: "",
        draftGenerationStatus: "",
        draftError: error instanceof Error ? error.message : "正文生成提交失败",
      });
    }
  },

  startDraftBlockEdit(event: DatasetEvent) {
    const blockId = datasetString(event, "blockId");
    const block = this.data.draftBlocks.find((item) => item.id === blockId);
    if (!block || block.isLegacy || block.busy) {
      return;
    }
    this.setData({
      editingBlockId: block.id,
      editingBlockContent: block.current_version.content,
      draftError: "",
    });
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
      await this.loadDraftBlocks(true);
    } catch (error) {
      if (routeAuthError(error, "me")) {
        return;
      }
      this.setData({ draftError: error instanceof Error ? error.message : "正文保存失败" });
    } finally {
      this.patchDraftBlock(block.id, { busy: false });
    }
  },

  toggleDraftBlockSources(event: DatasetEvent) {
    const blockId = datasetString(event, "blockId");
    this.setData({ sourceBlockId: this.data.sourceBlockId === blockId ? "" : blockId });
  },

  async confirmDraftBlock(event: DatasetEvent) {
    const blockId = datasetString(event, "blockId");
    const block = this.data.draftBlocks.find((item) => item.id === blockId);
    if (!block || block.busy || block.editorial_status === "confirmed") {
      return;
    }
    this.patchDraftBlock(block.id, { busy: true });
    try {
      await decideMemoirDraftBlock(this.data.projectId, block.id, "confirm", {
        expected_current_version_id: block.current_version_id,
        idempotency_key: `workspace:confirm:${block.id}:${Date.now()}`,
        reason: "在章节工作台确认正文块",
      });
      await this.loadDraftBlocks(true);
    } catch (error) {
      if (routeAuthError(error, "me")) {
        return;
      }
      this.setData({ draftError: error instanceof Error ? error.message : "正文确认失败" });
    } finally {
      this.patchDraftBlock(block.id, { busy: false });
    }
  },

  async toggleDraftBlockLock(event: DatasetEvent) {
    const blockId = datasetString(event, "blockId");
    const block = this.data.draftBlocks.find((item) => item.id === blockId);
    if (!block || block.busy || block.isLegacy) {
      return;
    }
    const action = block.locked ? "unlock" : "lock";
    this.patchDraftBlock(block.id, { busy: true });
    try {
      await decideMemoirDraftBlock(this.data.projectId, block.id, action, {
        expected_current_version_id: block.current_version_id,
        idempotency_key: `workspace:${action}:${block.id}:${Date.now()}`,
        reason: block.locked ? "继续编辑当前正文块" : "保护已经认可的正文措辞",
      });
      await this.loadDraftBlocks(true);
    } catch (error) {
      if (routeAuthError(error, "me")) {
        return;
      }
      this.setData({ draftError: error instanceof Error ? error.message : "正文锁定状态保存失败" });
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
      || block.isLegacy
      || block.locked
      || block.busy
      || this.data.revisionBlockId
      || this.data.draftGenerationBusy
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
      revisionStatus: "正在提交修改建议",
      draftError: "",
    });
    try {
      const job = await enqueueMemoirDraftBlockRevision(this.data.projectId, block.id, {
        expected_current_version_id: block.current_version_id,
        feedback,
      });
      await this.applyDraftJobState(job, "revise", block.id);
    } catch (error) {
      if (routeAuthError(error, "me")) {
        return;
      }
      this.setData({
        revisionBlockId: "",
        revisionJobId: "",
        revisionStatus: "",
        draftError: error instanceof Error ? error.message : "修改建议提交失败",
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
    if (!block || block.busy || this.data.revisionBlockId !== block.id || !this.data.revisionJobId) {
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
      await this.loadDraftBlocks(true);
    } catch (error) {
      if (routeAuthError(error, "me")) {
        return;
      }
      this.setData({ draftError: error instanceof Error ? error.message : "修改建议应用失败" });
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
      this.setData({ draftGenerationStatus: `已组装章节稿 v${result.version}` });
      wx.showToast({ title: "章节稿已组装", icon: "success" });
    } catch (error) {
      if (routeAuthError(error, "me")) {
        return;
      }
      this.setData({ draftError: error instanceof Error ? error.message : "章节稿组装失败" });
    } finally {
      this.setData({ assemblingDraft: false });
    }
  },

  async startHumanFollowupInterview(event: DatasetEvent) {
    const taskId = datasetString(event, "taskId");
    if (!taskId || this.data.startingFollowupTaskId) {
      return;
    }
    const task = this.data.followupTasks.find((item) => item.id === taskId);
    if (!task?.canInterview) {
      return;
    }
    this.setData({ startingFollowupTaskId: taskId, followupError: "" });
    try {
      const detail = await createMemoirVoiceSession({
        guidance_mode: "guided_interview",
        interview_type: "human_interview",
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
          interview_type: "human_interview",
          session_goal: "book_completion",
          entry: "chapter_workspace",
        },
        { projectId: this.data.projectId, voiceSessionId: detail.session.id },
      );
      wx.navigateTo({
        url: pageUrl("/pages/voice-chat/index", {
          voiceSessionId: detail.session.id,
          mode: "guided_interview",
          interviewType: "human_interview",
          sessionGoal: "book_completion",
          projectTitle: this.data.workspace?.chapter_title || "补访问题",
        }),
      });
    } catch (error) {
      if (routeAuthError(error, "me")) {
        return;
      }
      this.setData({ followupError: error instanceof Error ? error.message : "人工访谈创建失败" });
    } finally {
      this.setData({ startingFollowupTaskId: "" });
    }
  },

  async onPullDownRefresh() {
    await this.loadWorkspace(true);
    wx.stopPullDownRefresh();
  },

  onShow() {
    if (this.loadedOnce) {
      void this.loadWorkspace(true);
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
