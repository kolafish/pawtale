import type {
  MemoirChapterWorkspace,
  MemoirChapterWorkspaceAnswerUpdate,
  MemoirChapterWorkspaceAvailableStory,
  MemoirChapterWorkspaceClaim,
  MemoirChapterWorkspaceNode,
  MemoirChapterWorkspaceStory,
  MemoirChapterWorkspaceStorySlot,
  MemoirNarrativeGap,
  MemoirNarrativeGapAction,
  MemoirStorySlotResolutionStatus,
  MemoirVerificationDecision,
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
  applyMemoirDraftBlockRevision,
  assembleMemoirChapterDraftBlocks,
  confirmMemoirBookChapterDraft,
  confirmMemoirChapterContract,
  createMemoirChapterContractRevision,
  createMemoirVoiceSession,
  decideMemoirClaim,
  decideMemoirDraftBlock,
  decideMemoirStory,
  enqueueMemoirDraftBlockGeneration,
  enqueueMemoirDraftBlockRevision,
  enqueueMemoirChapterWorkspaceAudit,
  excludeMemoirStoryFromChapter,
  getMemoirBookPlan,
  getMemoirDraftBlockJob,
  getMemoirChapterWorkspace,
  includeMemoirStoryInChapter,
  listMemoirDraftBlocks,
  listMemoirFollowupTasks,
  manuallyReviseMemoirDraftBlock,
  mergeMemoirChapterStructures,
  mergeMemoirStory,
  moveMemoirStoryBetweenChapterNodes,
  reorderMemoirChapterNodes,
  resolveMemoirNarrativeGap,
  resolveMemoirStorySlot,
  splitMemoirChapterStructure,
  suggestMemoirChapterDirection,
  updateMemoirChapterNode,
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
import { createChapterStructureActions } from "./chapter-structure-actions";
import type { ChapterMergeOption } from "./chapter-structure-editor";
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


const TAB_VALUES = ["overview", "structure", "stories", "drafts", "checks", "followups"] as const;
const GAP_ACTIONS = ["resolve", "accept-incomplete", "not-applicable", "dismiss"] as const;
const VERIFICATION_DECISIONS = ["confirm", "reject"] as const;
const SLOT_RESOLUTIONS = ["accepted_incomplete", "not_applicable"] as const;
const AUDIT_POLL_INTERVAL_MS = 1500;
const DRAFT_JOB_POLL_INTERVAL_MS = 1500;
const AVAILABLE_STORY_PREVIEW_LIMIT = 8;

type WorkspaceTab = (typeof TAB_VALUES)[number];

interface WorkspaceDataset {
  tab?: string;
  gapId?: string;
  action?: string;
  taskId?: string;
  blockId?: string;
  storyId?: string;
  claimId?: string;
  slotKey?: string;
  decision?: string;
  direction?: string;
  nodeId?: string;
}

type DatasetEvent = PageDatasetEvent<WorkspaceDataset>;
type PickerDatasetEvent = DatasetEvent & { detail: { value: string | number } };

interface NodeView extends MemoirChapterWorkspaceNode {
  statusText: string;
  storyText: string;
  canMoveUp: boolean;
  canMoveDown: boolean;
  selectedForSplit: boolean;
}

interface StorySlotView extends MemoirChapterWorkspaceStorySlot {
  labelText: string;
  statusText: string;
  statusClass: string;
  actionKey: string;
  canResolve: boolean;
  busy: boolean;
}

interface ClaimView extends MemoirChapterWorkspaceClaim {
  evidenceCountText: string;
  statusText: string;
  canDecide: boolean;
  busy: boolean;
}

interface AnswerUpdateView extends MemoirChapterWorkspaceAnswerUpdate {
  summaryText: string;
}

interface StoryView extends MemoirChapterWorkspaceStory {
  statusText: string;
  detailCountText: string;
  expanded: boolean;
  canDecide: boolean;
  canExcludeFromChapter: boolean;
  canMerge: boolean;
  canMoveWithinChapter: boolean;
  mergeTargetIds: string[];
  mergeTargetTitles: string[];
  moveSourceNodeId: string;
  moveTargetNodeIds: string[];
  moveTargetNodeTitles: string[];
  busy: boolean;
  slots: StorySlotView[];
  claims: ClaimView[];
}

interface AvailableStoryView extends MemoirChapterWorkspaceAvailableStory {
  statusText: string;
  actionText: string;
  busy: boolean;
  error: string;
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
  nextAction: ChapterNextAction;
  workspace: MemoirChapterWorkspace | null;
  contractStatusText: string;
  auditStatusText: string;
  auditError: string;
  auditBusy: boolean;
  actionBusyGapId: string;
  contractActionBusy: boolean;
  contractActionStatus: string;
  contractEditorOpen: boolean;
  contractObjective: string;
  contractReaderTakeaway: string;
  contractTimeScopeText: string;
  contractQuestionsText: string;
  structureActionBusy: boolean;
  topologyActionStatus: string;
  topologyError: string;
  splitEditorOpen: boolean;
  splitNodeIds: string[];
  splitTitle: string;
  splitObjective: string;
  splitReaderTakeaway: string;
  splitTimeScopeText: string;
  splitQuestionsText: string;
  mergeOptions: ChapterMergeOption[];
  mergeTargetChapterId: string;
  mergeTargetTitle: string;
  editingNodeId: string;
  editingNodeTitle: string;
  editingNodePurpose: string;
  editingNodeRequired: boolean;
  busyStoryIds: string[];
  busyClaimIds: string[];
  busySlotKeys: string[];
  expandedStoryIds: string[];
  nodes: NodeView[];
  stories: StoryView[];
  storyReviewProgressText: string;
  availableStories: AvailableStoryView[];
  availableStoryQuery: string;
  availableStoryTotalCount: number;
  availableStoryMatchCount: number;
  availableStoryHasMore: boolean;
  showAllAvailableStories: boolean;
  appliedAnswerUpdates: AnswerUpdateView[];
  contractRevisionBusy: boolean;
  contractRevisionError: string;
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
  const verification = story.verification_status === "confirmed" ? "故事已确认" : "故事待确认";
  const completion = ({
    incomplete: "待补充",
    sufficient: "内容充分",
    accepted_incomplete: "接受不完整",
  })[story.completion_status] || story.completion_status;
  return `${verification} · ${completion}`;
}

function claimStatusText(status: string): string {
  return ({
    unreviewed: "待核对",
    needs_verification: "需核对",
    confirmed: "已确认",
    disputed: "有冲突",
    rejected: "已否定",
  })[status] || status;
}

function isVerificationDecision(value: string): value is MemoirVerificationDecision {
  return VERIFICATION_DECISIONS.includes(value as MemoirVerificationDecision);
}

function isSlotResolution(value: string): value is MemoirStorySlotResolutionStatus {
  return SLOT_RESOLUTIONS.includes(value as MemoirStorySlotResolutionStatus);
}

function confirmDialog(title: string, content: string, confirmText: string): Promise<boolean> {
  return new Promise((resolve) => {
    wx.showModal({
      title,
      content,
      confirmText,
      success: (result) => resolve(Boolean(result.confirm)),
      fail: () => resolve(false),
    });
  });
}

const chapterStructureActions = createChapterStructureActions(confirmDialog, {
  createContractRevision: createMemoirChapterContractRevision,
  getBookPlan: getMemoirBookPlan,
  splitChapter: splitMemoirChapterStructure,
  mergeChapters: mergeMemoirChapterStructures,
});

function availableStoryStatusText(story: MemoirChapterWorkspaceAvailableStory): string {
  if (story.verification_status === "needs_verification" || story.verification_status === "unreviewed") {
    return "待核实";
  }
  return ({
    incomplete: "待补充",
    sufficient: "内容充分",
    accepted_incomplete: "接受不完整",
  })[story.completion_status] || story.completion_status;
}

function matchesAvailableStory(
  story: MemoirChapterWorkspaceAvailableStory,
  query: string,
): boolean {
  if (!query) {
    return true;
  }
  return `${story.title}\n${story.summary}`.toLocaleLowerCase().includes(query);
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
      sourceCountText: sourceCount ? `${sourceCount} 项来源` : "没有详细来源",
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
  availableStoryState: new Map<string, { busy: boolean; error: string }>(),
  loadedOnce: false,
  ...chapterStructureActions,

  data: {
    loading: false,
    error: "",
    projectId: "",
    chapterId: "",
    activeTab: "overview",
    nextAction: LOADING_NEXT_ACTION,
    workspace: null,
    contractStatusText: "本章方向尚未确定",
    auditStatusText: "尚未检查",
    auditError: "",
    auditBusy: false,
    actionBusyGapId: "",
    contractActionBusy: false,
    contractActionStatus: "",
    contractEditorOpen: false,
    contractObjective: "",
    contractReaderTakeaway: "",
    contractTimeScopeText: "",
    contractQuestionsText: "",
    structureActionBusy: false,
    topologyActionStatus: "",
    topologyError: "",
    splitEditorOpen: false,
    splitNodeIds: [],
    splitTitle: "",
    splitObjective: "",
    splitReaderTakeaway: "",
    splitTimeScopeText: "",
    splitQuestionsText: "",
    mergeOptions: [],
    mergeTargetChapterId: "",
    mergeTargetTitle: "",
    editingNodeId: "",
    editingNodeTitle: "",
    editingNodePurpose: "",
    editingNodeRequired: false,
    busyStoryIds: [],
    busyClaimIds: [],
    busySlotKeys: [],
    expandedStoryIds: [],
    nodes: [],
    stories: [],
    storyReviewProgressText: "0 / 0 已核对",
    availableStories: [],
    availableStoryQuery: "",
    availableStoryTotalCount: 0,
    availableStoryMatchCount: 0,
    availableStoryHasMore: false,
    showAllAvailableStories: false,
    appliedAnswerUpdates: [],
    contractRevisionBusy: false,
    contractRevisionError: "",
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
    const activeTab = queryChoice(query, "tab", TAB_VALUES, "overview");
    this.setData({ projectId, chapterId, activeTab, error: "" });
    if (!projectId || !chapterId) {
      this.setData({ error: "缺少章节信息" });
      return;
    }
    await this.loadWorkspace();
    if (
      queryParam(query, "editContract") === "1"
      && this.data.workspace?.contract?.status === "draft"
    ) {
      this.openContractEditor(this.data.workspace.contract);
      this.setData({
        contractActionStatus: "请核对合并后的章节目标、范围和核心问题，再确认结构",
      });
    }
    await this.restoreDraftJob();
    this.loadedOnce = true;
  },

  applyWorkspace(workspace: MemoirChapterWorkspace) {
    const storyTitles = new Map(workspace.stories.map((story) => [story.id, story.title]));
    const contractDraft = workspace.contract?.status === "draft";
    const storyNodes = (workspace.contract?.nodes || []).filter(
      (node) => node.node_type === "story",
    );
    const linkedNodeIds = new Map<string, string[]>();
    for (const node of workspace.contract?.nodes || []) {
      for (const link of node.stories) {
        linkedNodeIds.set(link.story_id, [
          ...(linkedNodeIds.get(link.story_id) || []),
          node.id,
        ]);
      }
    }
    const busyStoryIds = new Set(this.data.busyStoryIds);
    const busyClaimIds = new Set(this.data.busyClaimIds);
    const busySlotKeys = new Set(this.data.busySlotKeys);
    const workspaceStoryIds = new Set(workspace.stories.map((story) => story.id));
    const expandedStoryIds = new Set(
      this.data.expandedStoryIds.filter((storyId) => workspaceStoryIds.has(storyId)),
    );
    const splitNodeIds = new Set(this.data.splitNodeIds);
    const availableStoryQuery = this.data.availableStoryQuery.trim().toLocaleLowerCase();
    const allAvailableStories = workspace.available_stories.map((story) => ({
      ...story,
      statusText: availableStoryStatusText(story),
      actionText: story.excluded_from_chapter ? "重新加入" : "加入本章",
      busy: this.availableStoryState.get(story.id)?.busy || false,
      error: this.availableStoryState.get(story.id)?.error || "",
    }));
    const matchingAvailableStories = allAvailableStories.filter((story) => (
      matchesAvailableStory(story, availableStoryQuery)
    ));
    const visibleAvailableStories = availableStoryQuery || this.data.showAllAvailableStories
      ? matchingAvailableStories
      : matchingAvailableStories.slice(0, AVAILABLE_STORY_PREVIEW_LIMIT);
    const notes = new Map(
      this.data.gapGroups.flatMap((group) => group.items.map((gap) => [gap.id, gap.note] as const)),
    );
    const state = auditState(workspace);
    const openGap = (gap: MemoirNarrativeGap) => gapView(gap, notes.get(gap.id) || "");
    this.setData({
      workspace,
      contractStatusText: workspace.contract
        ? `本章方向${workspace.contract.status === "confirmed" ? "已确认" : "待确认"}`
        : "本章方向尚未确定",
      auditStatusText: state.text,
      auditError: state.error,
      auditBusy: state.busy,
      expandedStoryIds: [...expandedStoryIds],
      nodes: (workspace.contract?.nodes || []).map((node, index, nodes) => ({
        ...node,
        statusText: nodeStatusText(node.status),
        storyText: node.stories.length
          ? node.stories.map((link) => storyTitles.get(link.story_id) || "未知故事").join("、")
          : "尚未关联故事",
        canMoveUp: Boolean(contractDraft && index > 0),
        canMoveDown: Boolean(contractDraft && index < nodes.length - 1),
        selectedForSplit: this.data.splitEditorOpen && splitNodeIds.has(node.id),
      })),
      stories: workspace.stories.map((story) => {
        const currentNodeIds = linkedNodeIds.get(story.id) || [];
        const moveTargets = storyNodes.filter((node) => !currentNodeIds.includes(node.id));
        const mergeTargets = workspace.stories.filter(
          (candidate) => candidate.id !== story.id && candidate.story_type === story.story_type,
        );
        const pendingClaimCount = story.claims.filter(
          (claim) => !["confirmed", "rejected"].includes(claim.verification_status),
        ).length;
        const pendingSlotCount = story.slots.filter(
          (slot) => ["missing", "partial"].includes(slot.status),
        ).length;
        const pendingDetailCount = pendingClaimCount + pendingSlotCount;
        return {
          ...story,
          statusText: storyStatusText(story),
          detailCountText: pendingDetailCount
            ? `${pendingDetailCount} 项待核对`
            : "具体内容已核对",
          expanded: expandedStoryIds.has(story.id),
          canDecide: story.publication_status === "eligible",
          canExcludeFromChapter: Boolean(contractDraft),
          canMerge: Boolean(contractDraft && mergeTargets.length),
          canMoveWithinChapter: Boolean(
            contractDraft && currentNodeIds.length === 1 && moveTargets.length,
          ),
          mergeTargetIds: mergeTargets.map((candidate) => candidate.id),
          mergeTargetTitles: mergeTargets.map((candidate) => candidate.title),
          moveSourceNodeId: currentNodeIds.length === 1 ? currentNodeIds[0] : "",
          moveTargetNodeIds: moveTargets.map((node) => node.id),
          moveTargetNodeTitles: moveTargets.map((node) => node.title),
          busy: busyStoryIds.has(story.id),
          slots: story.slots.map((slot) => {
            const status = slotStatus(slot.status);
            const actionKey = `${story.id}:${slot.slot_key}`;
            return {
              ...slot,
              labelText: SLOT_LABELS[slot.slot_key] || "其他内容",
              statusText: status.text,
              statusClass: status.className,
              actionKey,
              canResolve: ["missing", "partial"].includes(slot.status),
              busy: busySlotKeys.has(actionKey),
            };
          }),
          claims: story.claims.map((claim) => ({
            ...claim,
            evidenceCountText: `${claim.evidence.length} 段原话依据`,
            statusText: claimStatusText(claim.verification_status),
            canDecide: claim.publication_status === "eligible",
            busy: busyClaimIds.has(claim.id),
          })),
        };
      }),
      storyReviewProgressText: `${workspace.stories.filter(
        (story) => ["confirmed", "rejected"].includes(story.verification_status),
      ).length} / ${workspace.stories.length} 已核对`,
      availableStories: visibleAvailableStories,
      availableStoryTotalCount: allAvailableStories.length,
      availableStoryMatchCount: matchingAvailableStories.length,
      availableStoryHasMore: !availableStoryQuery
        && visibleAvailableStories.length < matchingAvailableStories.length,
      appliedAnswerUpdates: workspace.answer_updates
        .filter((item) => item.status === "applied")
        .slice(0, 3)
        .map((item) => ({
          ...item,
          summaryText: item.summaries.join("\n"),
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
      nextAction: chapterNextAction(workspace, this.data.followupTasks, this.data.draftBlocks),
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
        this.loadChapterOptions(),
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

  async refreshWorkspaceOnly() {
    const workspace = await getMemoirChapterWorkspace(this.data.projectId, this.data.chapterId);
    this.applyWorkspace(workspace);
  },

  setStoryBusy(storyId: string, busy: boolean) {
    const busyStoryIds = busy
      ? [...new Set([...this.data.busyStoryIds, storyId])]
      : this.data.busyStoryIds.filter((id) => id !== storyId);
    this.setData({
      busyStoryIds,
      stories: this.data.stories.map((story) => (
        story.id === storyId ? { ...story, busy } : story
      )),
    });
  },

  toggleStoryDetails(event: DatasetEvent) {
    const storyId = datasetString(event, "storyId");
    if (!storyId || !this.data.stories.some((story) => story.id === storyId)) {
      return;
    }
    const expandedStoryIds = this.data.expandedStoryIds.includes(storyId)
      ? this.data.expandedStoryIds.filter((id) => id !== storyId)
      : [...this.data.expandedStoryIds, storyId];
    this.setData({
      expandedStoryIds,
      stories: this.data.stories.map((story) => (
        story.id === storyId ? { ...story, expanded: !story.expanded } : story
      )),
    });
  },

  setClaimBusy(claimId: string, busy: boolean) {
    const busyClaimIds = busy
      ? [...new Set([...this.data.busyClaimIds, claimId])]
      : this.data.busyClaimIds.filter((id) => id !== claimId);
    this.setData({
      busyClaimIds,
      stories: this.data.stories.map((story) => ({
        ...story,
        claims: story.claims.map((claim) => (
          claim.id === claimId ? { ...claim, busy } : claim
        )),
      })),
    });
  },

  setSlotBusy(actionKey: string, busy: boolean) {
    const busySlotKeys = busy
      ? [...new Set([...this.data.busySlotKeys, actionKey])]
      : this.data.busySlotKeys.filter((key) => key !== actionKey);
    this.setData({
      busySlotKeys,
      stories: this.data.stories.map((story) => ({
        ...story,
        slots: story.slots.map((slot) => (
          slot.actionKey === actionKey ? { ...slot, busy } : slot
        )),
      })),
    });
  },

  startNodeEdit(event: DatasetEvent) {
    const nodeId = datasetString(event, "nodeId");
    const node = this.data.nodes.find((candidate) => candidate.id === nodeId);
    if (
      !node
      || this.data.workspace?.contract?.status !== "draft"
      || this.data.structureActionBusy
      || this.data.contractActionBusy
    ) {
      return;
    }
    this.setData({
      editingNodeId: node.id,
      editingNodeTitle: node.title,
      editingNodePurpose: node.purpose,
      editingNodeRequired: node.required,
      error: "",
    });
  },

  changeNodeTitle(event: PageTextInputEvent) {
    this.setData({ editingNodeTitle: event.detail.value });
  },

  changeNodePurpose(event: PageTextInputEvent) {
    this.setData({ editingNodePurpose: event.detail.value });
  },

  changeNodeRequired(event: { detail: { value: boolean } }) {
    this.setData({ editingNodeRequired: event.detail.value });
  },

  cancelNodeEdit() {
    this.setData({
      editingNodeId: "",
      editingNodeTitle: "",
      editingNodePurpose: "",
      editingNodeRequired: false,
    });
  },

  async saveNodeEdit() {
    const contract = this.data.workspace?.contract;
    const title = this.data.editingNodeTitle.trim();
    const purpose = this.data.editingNodePurpose.trim();
    if (
      !contract
      || contract.status !== "draft"
      || !this.data.editingNodeId
      || this.data.structureActionBusy
      || this.data.contractActionBusy
    ) {
      return;
    }
    if (!title || !purpose) {
      this.setData({ error: "节点标题和作用都不能为空" });
      return;
    }
    this.setData({ structureActionBusy: true, error: "" });
    let saved = false;
    try {
      await updateMemoirChapterNode(
        this.data.projectId,
        contract.id,
        this.data.editingNodeId,
        { title, purpose, required: this.data.editingNodeRequired },
      );
      saved = true;
      this.cancelNodeEdit();
      await this.refreshWorkspaceOnly();
      wx.showToast({ title: "节点已更新", icon: "success" });
    } catch (error) {
      if (routeAuthError(error, "me")) {
        return;
      }
      this.setData({
        error: saved
          ? "节点已保存，但页面刷新失败，请重新进入本页查看最新状态"
          : (error instanceof Error ? error.message : "节点保存失败"),
      });
    } finally {
      this.setData({ structureActionBusy: false });
    }
  },

  async moveNode(event: DatasetEvent) {
    const contract = this.data.workspace?.contract;
    const nodeId = datasetString(event, "nodeId");
    const direction = datasetString(event, "direction");
    const index = this.data.nodes.findIndex((node) => node.id === nodeId);
    const targetIndex = direction === "up" ? index - 1 : index + 1;
    if (
      !contract
      || contract.status !== "draft"
      || !nodeId
      || !["up", "down"].includes(direction)
      || targetIndex < 0
      || targetIndex >= this.data.nodes.length
      || this.data.structureActionBusy
      || this.data.contractActionBusy
    ) {
      return;
    }
    const ordered = this.data.nodes.map((node) => node.id);
    [ordered[index], ordered[targetIndex]] = [ordered[targetIndex], ordered[index]];
    this.setData({ structureActionBusy: true, error: "" });
    let saved = false;
    try {
      await reorderMemoirChapterNodes(this.data.projectId, contract.id, {
        node_ids: ordered,
      });
      saved = true;
      await this.refreshWorkspaceOnly();
    } catch (error) {
      if (routeAuthError(error, "me")) {
        return;
      }
      this.setData({
        error: saved
          ? "节点顺序已保存，但页面刷新失败，请重新进入本页查看最新状态"
          : (error instanceof Error ? error.message : "节点排序失败"),
      });
    } finally {
      this.setData({ structureActionBusy: false });
    }
  },

  async moveStoryToNode(event: PickerDatasetEvent) {
    const storyId = datasetString(event, "storyId");
    const story = this.data.stories.find((candidate) => candidate.id === storyId);
    const targetNodeId = story?.moveTargetNodeIds[Number(event.detail.value)] || "";
    const contract = this.data.workspace?.contract;
    if (
      !story
      || !contract
      || contract.status !== "draft"
      || !story.moveSourceNodeId
      || !targetNodeId
      || story.busy
      || this.data.structureActionBusy
      || this.data.contractActionBusy
    ) {
      return;
    }
    this.setStoryBusy(story.id, true);
    this.setData({ structureActionBusy: true, error: "" });
    let saved = false;
    try {
      await moveMemoirStoryBetweenChapterNodes(
        this.data.projectId,
        contract.id,
        story.id,
        {
          source_node_id: story.moveSourceNodeId,
          target_node_id: targetNodeId,
        },
      );
      saved = true;
      await this.refreshWorkspaceOnly();
      wx.showToast({ title: "故事节点已调整", icon: "success" });
    } catch (error) {
      if (routeAuthError(error, "me")) {
        return;
      }
      this.setData({
        error: saved
          ? "故事节点已保存，但页面刷新失败，请重新进入本页查看最新状态"
          : (error instanceof Error ? error.message : "故事节点调整失败"),
      });
    } finally {
      this.setStoryBusy(story.id, false);
      this.setData({ structureActionBusy: false });
    }
  },

  async mergeStory(event: PickerDatasetEvent) {
    const sourceStoryId = datasetString(event, "storyId");
    const source = this.data.stories.find((story) => story.id === sourceStoryId);
    const retainedStoryId = source?.mergeTargetIds[Number(event.detail.value)] || "";
    const retained = this.data.stories.find((story) => story.id === retainedStoryId);
    if (
      !source
      || !retained
      || this.data.workspace?.contract?.status !== "draft"
      || source.busy
      || retained.busy
      || this.data.structureActionBusy
      || this.data.contractActionBusy
    ) {
      return;
    }
    if (!await confirmDialog(
      "合并重复故事",
      `“${source.title}”的事实和来源会并入“${retained.title}”，所有章节都会受影响。`,
      "确认合并",
    )) {
      return;
    }
    this.setData({ structureActionBusy: true, error: "" });
    this.setStoryBusy(source.id, true);
    this.setStoryBusy(retained.id, true);
    let saved = false;
    try {
      await mergeMemoirStory(this.data.projectId, source.id, {
        retained_story_id: retained.id,
        idempotency_key: `workspace:story:${source.id}:merge:${retained.id}:${Date.now()}`,
        reason: "章节工作台人工确认重复故事",
      });
      saved = true;
      await this.refreshWorkspaceOnly();
      wx.showToast({ title: "重复故事已合并", icon: "success" });
    } catch (error) {
      if (routeAuthError(error, "me")) {
        return;
      }
      this.setData({
        error: saved
          ? "故事已合并，但页面刷新失败，请重新进入本页查看最新状态"
          : (error instanceof Error ? error.message : "故事合并失败"),
      });
    } finally {
      this.setStoryBusy(source.id, false);
      this.setStoryBusy(retained.id, false);
      this.setData({ structureActionBusy: false });
    }
  },

  async confirmContract() {
    const contract = this.data.workspace?.contract;
    if (
      !contract
      || contract.status === "confirmed"
      || this.data.contractActionBusy
      || this.data.structureActionBusy
    ) {
      return;
    }
    const confirmed = await confirmDialog(
      "确认章节结构",
      "确认后章节目标和节点结构会锁定；故事和事实仍可继续核对。",
      "确认结构",
    );
    if (!confirmed) {
      return;
    }
    this.setData({
      contractActionBusy: true,
      contractActionStatus: "正在确认章节结构",
      error: "",
    });
    let saved = false;
    try {
      await confirmMemoirChapterContract(this.data.projectId, contract.id);
      saved = true;
      await this.refreshWorkspaceOnly();
      this.setData({ contractActionStatus: "章节结构已确认" });
      wx.showToast({ title: "章节结构已确认", icon: "success" });
    } catch (error) {
      if (routeAuthError(error, "me")) {
        return;
      }
      this.setData({
        contractActionStatus: "",
        error: saved
          ? "章节结构已保存，但页面刷新失败，请重新进入本页查看最新状态"
          : (error instanceof Error ? error.message : "章节结构确认失败"),
      });
    } finally {
      this.setData({ contractActionBusy: false });
    }
  },

  async decideStory(event: DatasetEvent) {
    const storyId = datasetString(event, "storyId");
    const decision = datasetString(event, "decision");
    if (
      !storyId
      || !isVerificationDecision(decision)
      || this.data.busyStoryIds.includes(storyId)
    ) {
      return;
    }
    if (
      decision === "reject"
      && !await confirmDialog(
        "确认故事有误",
        "这会将故事标记为有误，并影响所有引用它的章节。",
        "故事有误",
      )
    ) {
      return;
    }
    this.setStoryBusy(storyId, true);
    this.setData({ error: "" });
    let saved = false;
    try {
      await decideMemoirStory(this.data.projectId, storyId, decision, {
        idempotency_key: `workspace:story:${storyId}:${decision}:${Date.now()}`,
        reason: decision === "confirm" ? "章节工作台人工确认" : "章节工作台人工否定",
      });
      saved = true;
      await this.refreshWorkspaceOnly();
      wx.showToast({ title: decision === "confirm" ? "故事已确认" : "故事已否定", icon: "success" });
    } catch (error) {
      if (routeAuthError(error, "me")) {
        return;
      }
      this.setData({
        error: saved
          ? "故事状态已保存，但页面刷新失败，请重新进入本页查看最新状态"
          : (error instanceof Error ? error.message : "故事状态保存失败"),
      });
    } finally {
      this.setStoryBusy(storyId, false);
    }
  },

  async excludeStoryFromChapter(event: DatasetEvent) {
    const storyId = datasetString(event, "storyId");
    const contract = this.data.workspace?.contract;
    if (
      !storyId
      || !contract
      || contract.status !== "draft"
      || this.data.busyStoryIds.includes(storyId)
      || this.data.structureActionBusy
      || this.data.contractActionBusy
    ) {
      return;
    }
    if (!await confirmDialog(
      "移出本章",
      "故事本身会保留，只是不再用于当前章节。",
      "移出本章",
    )) {
      return;
    }
    this.setStoryBusy(storyId, true);
    this.setData({ structureActionBusy: true, error: "" });
    let saved = false;
    try {
      await excludeMemoirStoryFromChapter(
        this.data.projectId,
        contract.id,
        storyId,
        {
          idempotency_key: `workspace:contract:${contract.id}:exclude:${storyId}:${Date.now()}`,
          reason: "用户从当前章节移除故事",
        },
      );
      saved = true;
      await this.refreshWorkspaceOnly();
      wx.showToast({ title: "已移出本章", icon: "success" });
    } catch (error) {
      if (routeAuthError(error, "me")) {
        return;
      }
      this.setData({
        error: saved
          ? "章节结构已保存，但页面刷新失败，请重新进入本页查看最新状态"
          : (error instanceof Error ? error.message : "移出本章失败"),
      });
    } finally {
      this.setStoryBusy(storyId, false);
      this.setData({ structureActionBusy: false });
    }
  },

  async decideClaim(event: DatasetEvent) {
    const claimId = datasetString(event, "claimId");
    const decision = datasetString(event, "decision");
    if (
      !claimId
      || !isVerificationDecision(decision)
      || this.data.busyClaimIds.includes(claimId)
    ) {
      return;
    }
    if (
      decision === "reject"
      && !await confirmDialog("否定这条事实", "这条事实及依赖它的正文会重新进入待处理状态。", "否定事实")
    ) {
      return;
    }
    this.setClaimBusy(claimId, true);
    this.setData({ error: "" });
    let saved = false;
    try {
      await decideMemoirClaim(this.data.projectId, claimId, decision, {
        idempotency_key: `workspace:claim:${claimId}:${decision}:${Date.now()}`,
        reason: decision === "confirm" ? "章节工作台人工确认" : "章节工作台人工否定",
      });
      saved = true;
      await this.refreshWorkspaceOnly();
      wx.showToast({ title: decision === "confirm" ? "事实已确认" : "事实已否定", icon: "success" });
    } catch (error) {
      if (routeAuthError(error, "me")) {
        return;
      }
      this.setData({
        error: saved
          ? "事实状态已保存，但页面刷新失败，请重新进入本页查看最新状态"
          : (error instanceof Error ? error.message : "事实状态保存失败"),
      });
    } finally {
      this.setClaimBusy(claimId, false);
    }
  },

  async resolveStorySlot(event: DatasetEvent) {
    const storyId = datasetString(event, "storyId");
    const slotKey = datasetString(event, "slotKey");
    const decision = datasetString(event, "decision");
    const actionKey = `${storyId}:${slotKey}`;
    if (
      !storyId
      || !slotKey
      || !isSlotResolution(decision)
      || this.data.busySlotKeys.includes(actionKey)
    ) {
      return;
    }
    const accepted = await confirmDialog(
      decision === "accepted_incomplete" ? "接受信息不完整" : "标记为不适用",
      decision === "accepted_incomplete"
        ? "确认叙述者暂时无法补充这一项，成书时会保留这份不确定性。"
        : "确认这一项不适用于当前故事。",
      "确认",
    );
    if (!accepted) {
      return;
    }
    this.setSlotBusy(actionKey, true);
    this.setData({ error: "" });
    let saved = false;
    try {
      await resolveMemoirStorySlot(this.data.projectId, storyId, slotKey, decision, {
        idempotency_key: `workspace:slot:${actionKey}:${decision}:${Date.now()}`,
        note: decision === "accepted_incomplete" ? "用户确认暂时无法补充" : "用户确认该项不适用",
      });
      saved = true;
      await this.refreshWorkspaceOnly();
      wx.showToast({ title: "完整度决定已保存", icon: "success" });
    } catch (error) {
      if (routeAuthError(error, "me")) {
        return;
      }
      this.setData({
        error: saved
          ? "完整度决定已保存，但页面刷新失败，请重新进入本页查看最新状态"
          : (error instanceof Error ? error.message : "完整度决定保存失败"),
      });
    } finally {
      this.setSlotBusy(actionKey, false);
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
      const followupTasks = response.items.map(followupTaskView);
      this.setData({
        followupTasks,
        followupError: "",
        nextAction: chapterNextAction(this.data.workspace, followupTasks, this.data.draftBlocks),
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
      const draftBlocks = draftBlockViews(response.blocks, this.data.workspace, this.data.draftBlocks);
      this.setData({
        draftBlocks,
        draftError: "",
        nextAction: chapterNextAction(this.data.workspace, this.data.followupTasks, draftBlocks),
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
    this.setData({ activeTab: queryChoice({ tab: datasetString(event, "tab") }, "tab", TAB_VALUES, "overview") });
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
    } else if (action.kind === "continue_fact_confirmation") {
      await this.beginFollowupInterview(action.taskId || "");
    } else if (action.kind === "open_followups") {
      this.setData({ activeTab: "followups" });
    } else if (action.kind === "open_checks") {
      this.setData({ activeTab: "checks" });
    } else if (action.kind === "generate_draft") {
      await this.generateDraftBlocks();
    } else if (action.kind === "open_drafts") {
      this.setData({ activeTab: "drafts" });
    }
  },

  async suggestCurrentDirection() {
    if (this.data.workspace?.contract || this.data.contractRevisionBusy) {
      return;
    }
    this.setData({ contractRevisionBusy: true, contractRevisionError: "" });
    try {
      await suggestMemoirChapterDirection(this.data.projectId, this.data.chapterId);
      await this.loadWorkspace(true);
    } catch (error) {
      if (routeAuthError(error, "me")) {
        return;
      }
      this.setData({
        contractRevisionError: error instanceof Error ? error.message : "本章方向整理失败",
      });
    } finally {
      this.setData({ contractRevisionBusy: false });
    }
  },

  async confirmCurrentDirection() {
    const contract = this.data.workspace?.contract;
    if (!contract || contract.status === "confirmed" || this.data.contractRevisionBusy) {
      return;
    }
    this.setData({ contractRevisionBusy: true, contractRevisionError: "" });
    try {
      await confirmMemoirChapterContract(this.data.projectId, contract.id);
      await this.loadWorkspace(true);
      wx.showToast({ title: "本章方向已确认", icon: "success" });
    } catch (error) {
      if (routeAuthError(error, "me")) {
        return;
      }
      this.setData({
        contractRevisionError: error instanceof Error ? error.message : "本章方向确认失败",
      });
    } finally {
      this.setData({ contractRevisionBusy: false });
    }
  },

  async createContractRevision() {
    const contract = this.data.workspace?.contract;
    if (
      !contract
      || contract.status !== "confirmed"
      || this.data.contractRevisionBusy
    ) {
      return;
    }
    this.setData({ contractRevisionBusy: true, contractRevisionError: "" });
    try {
      await createMemoirChapterContractRevision(
        this.data.projectId,
        this.data.chapterId,
        {
          idempotency_key: `workspace:revision:${contract.id}`,
          objective: contract.objective,
          reader_takeaway: contract.reader_takeaway,
          time_scope_text: contract.time_scope_text,
          time_start: contract.time_start,
          time_end: contract.time_end,
          core_questions: contract.core_questions,
          constraints: contract.constraints,
          copy_from_contract_id: contract.id,
          copy_nodes: true,
        },
      );
      await this.loadWorkspace(true);
    } catch (error) {
      if (routeAuthError(error, "me")) {
        return;
      }
      this.setData({
        contractRevisionError: error instanceof Error ? error.message : "章节草稿创建失败",
      });
    } finally {
      this.setData({ contractRevisionBusy: false });
    }
  },

  patchAvailableStory(storyId: string, patch: Partial<AvailableStoryView>) {
    const current = this.availableStoryState.get(storyId) || { busy: false, error: "" };
    this.availableStoryState.set(storyId, {
      busy: patch.busy ?? current.busy,
      error: patch.error ?? current.error,
    });
    this.setData({
      availableStories: this.data.availableStories.map((story) => (
        story.id === storyId ? { ...story, ...patch } : story
      )),
    });
  },

  changeAvailableStoryQuery(event: PageTextInputEvent) {
    this.setData({
      availableStoryQuery: event.detail.value,
      showAllAvailableStories: false,
    });
    if (this.data.workspace) {
      this.applyWorkspace(this.data.workspace);
    }
  },

  clearAvailableStoryQuery() {
    this.setData({
      availableStoryQuery: "",
      showAllAvailableStories: false,
    });
    if (this.data.workspace) {
      this.applyWorkspace(this.data.workspace);
    }
  },

  toggleAvailableStories() {
    this.setData({
      showAllAvailableStories: !this.data.showAllAvailableStories,
    });
    if (this.data.workspace) {
      this.applyWorkspace(this.data.workspace);
    }
  },

  async includeAvailableStory(event: DatasetEvent) {
    const storyId = datasetString(event, "storyId");
    const contract = this.data.workspace?.contract;
    if (
      !storyId
      || !contract
      || contract.status !== "draft"
    ) {
      return;
    }
    const story = this.data.availableStories.find((item) => item.id === storyId);
    if (!story || story.busy) {
      return;
    }
    this.patchAvailableStory(story.id, { busy: true, error: "" });
    try {
      await includeMemoirStoryInChapter(
        this.data.projectId,
        contract.id,
        story.id,
        {
          idempotency_key: `workspace:include:${contract.id}:${story.id}:${Date.now()}`,
          reason: story.excluded_from_chapter ? "在章节工作台重新加入本章" : "在章节工作台加入本章",
        },
      );
      this.availableStoryState.delete(story.id);
      await this.loadWorkspace(true);
      wx.showToast({ title: "已加入本章", icon: "success" });
    } catch (error) {
      if (routeAuthError(error, "me")) {
        this.patchAvailableStory(story.id, { busy: false });
        return;
      }
      this.patchAvailableStory(story.id, {
        busy: false,
        error: error instanceof Error ? error.message : "内容加入本章失败",
      });
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
      await confirmMemoirBookChapterDraft(this.data.projectId, this.data.chapterId);
      this.setData({ draftGenerationStatus: `章节稿 v${result.version} 已确认` });
      wx.showToast({ title: "本章已确认", icon: "success" });
    } catch (error) {
      if (routeAuthError(error, "me")) {
        return;
      }
      this.setData({ draftError: error instanceof Error ? error.message : "章节稿确认失败" });
    } finally {
      this.setData({ assemblingDraft: false });
    }
  },

  async startFollowupInterview(event: DatasetEvent) {
    const taskId = datasetString(event, "taskId");
    await this.beginFollowupInterview(taskId);
  },

  async beginFollowupInterview(taskId: string) {
    if (!taskId || this.data.startingFollowupTaskId) {
      return;
    }
    const task = this.data.followupTasks.find((item) => item.id === taskId);
    if (task?.status === "in_progress" && task.assigned_session_id) {
      wx.navigateTo({
        url: pageUrl("/pages/voice-chat/index", {
          voiceSessionId: task.assigned_session_id,
          mode: "guided_interview",
          interviewType: "ai_interview",
          sessionGoal: "book_completion",
          projectTitle: this.data.workspace?.chapter_title || "补全本章",
        }),
      });
      return;
    }
    if (!task?.canInterview) {
      this.setData({ followupError: "这个问题当前不能开始，请刷新后再试" });
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
      wx.navigateTo({
        url: pageUrl("/pages/voice-chat/index", {
          voiceSessionId: detail.session.id,
          mode: "guided_interview",
          interviewType: "ai_interview",
          sessionGoal: "book_completion",
          projectTitle: this.data.workspace?.chapter_title || "补访问题",
        }),
      });
    } catch (error) {
      if (routeAuthError(error, "me")) {
        return;
      }
      this.setData({ followupError: error instanceof Error ? error.message : "AI 访谈创建失败" });
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
