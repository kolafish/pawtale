import type {
  MemoirChapterWorkspace,
  MemoirChapterWorkspaceStory,
} from "../../domain/chapter-workspace";
import type { MemoirDraftBlock } from "../../domain/draft-blocks";
import type { MemoirFollowupTask } from "../../domain/voice-chat";
import { chapterNextAction, type ChapterNextAction } from "./chapter-next-action";

const ACTIVE_FOLLOWUP_STATUSES = new Set(["pending", "deferred", "in_progress"]);

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
  resolutionText: string;
}

export interface AnswerUpdateView {
  id: string;
  question: string;
  summaryText: string;
}

export interface DraftSourceFactView {
  id: string;
  statement: string;
  evidenceText: string;
}

export interface DraftSourceStoryView {
  id: string;
  title: string;
}

export interface DraftBlockView extends MemoirDraftBlock {
  busy: boolean;
  sectionTitle: string;
  statusText: string;
  sourceCountText: string;
  sourceFacts: DraftSourceFactView[];
  sourceStories: DraftSourceStoryView[];
  revisionFeedback: string;
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
  canAssembleDraft: boolean;
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

function draftBlockView(
  block: MemoirDraftBlock,
  storyById: Map<string, MemoirChapterWorkspaceStory>,
  claimById: Map<string, MemoirChapterWorkspaceStory["claims"][number]>,
  existing: DraftBlockView[],
): DraftBlockView {
  const sectionTitle = ({
    opening: "开头",
    factual: "正文",
    transition: "衔接",
    reflection: "回望与感受",
    closing: "结尾",
    legacy_snapshot: "旧版正文",
  })[block.block_role];
  let statusText = block.editorial_status === "draft" ? "请通读" : "已保存";
  if (block.freshness_status === "stale") {
    statusText = block.locked ? "已保护，需核对新内容" : "新内容还未写入";
  } else if (block.locked) {
    statusText = "已保护";
  }
  const sourceFacts = block.current_version.claims.flatMap((source) => {
    const claim = claimById.get(source.claim_id);
    if (!claim) {
      return [];
    }
    const evidence = claim.evidence.find((item) => item.evidence_role === "supports");
    return [{
      id: claim.id,
      statement: claim.statement,
      evidenceText: (evidence?.quote || evidence?.evidence_note || "").trim(),
    }];
  });
  const sourceStories = block.current_version.stories.flatMap((source) => {
    const story = storyById.get(source.story_id);
    return story ? [{ id: story.id, title: story.title }] : [];
  });
  const sourceCount = sourceFacts.length + sourceStories.length;
  return {
    ...block,
    busy: existing.find((item) => item.id === block.id)?.busy || false,
    sectionTitle,
    statusText,
    sourceCountText: sourceCount ? `${sourceCount} 项口述依据` : "暂无详细依据",
    sourceFacts,
    sourceStories,
    revisionFeedback: existing.find((item) => item.id === block.id)?.revisionFeedback || "",
  };
}

export function draftBlockViews(
  blocks: MemoirDraftBlock[],
  workspace: MemoirChapterWorkspace,
  existing: DraftBlockView[] = [],
): DraftBlockView[] {
  const storyById = new Map(workspace.stories.map((story) => [story.id, story]));
  const claimById = new Map(
    workspace.stories.flatMap((story) => story.claims.map((claim) => [claim.id, claim] as const)),
  );
  return blocks
    .filter((block) => block.block_role !== "legacy_snapshot")
    .map((block) => draftBlockView(block, storyById, claimById, existing));
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
  const allActiveTasks = activeFollowupTasks(followupTasks);
  const eligibleTasks = allActiveTasks.filter((task) => (
    !confirmationTaskIds.has(task.id) && !mustHandleGapIds.has(task.gap_id)
  ));
  const directionGapIds = new Set<string>();
  const directionTasks = eligibleTasks.filter((task) => {
    if (directionGapIds.has(task.gap_id)) {
      return false;
    }
    directionGapIds.add(task.gap_id);
    return true;
  });
  const suggestionIds = new Set(workspace.gaps.suggested.map((gap) => gap.id));
  directionTasks.forEach((task) => suggestionIds.add(task.gap_id));
  const currentDraftBlocks = draftBlocks.filter((block) => block.block_role !== "legacy_snapshot");
  const mustConfirmCount = confirmations.length + reviewGaps.length;
  const suggestionCount = suggestionIds.size;
  const hasStaleDraft = currentDraftBlocks.some((block) => block.freshness_status === "stale");
  const canAssembleDraft = Boolean(currentDraftBlocks.length) && currentDraftBlocks.every((block) => (
    block.editorial_status === "confirmed"
    && block.freshness_status === "current"
  ));

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
    directions: directionTasks.slice(0, 3).map(directionView),
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
      taskId: allActiveTasks.find((task) => task.gap_id === gap.id)?.id || "",
      resolutionText: "",
    })),
    suggestionHints: workspace.gaps.suggested.slice(0, 3).map((gap) => ({
      id: gap.id,
      title: gap.title,
      description: gap.description,
      taskId: "",
      resolutionText: "",
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
    canAssembleDraft,
  };
}
