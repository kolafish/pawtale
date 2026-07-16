import type {
  MemoirChapterWorkspace,
  MemoirChapterWorkspaceStory,
} from "../../domain/chapter-workspace";
import type { MemoirChapterCompletionTask } from "../../domain/chapter-completion";
import type { MemoirDraftBlock } from "../../domain/draft-blocks";
import { chapterNextAction, type ChapterNextAction } from "./chapter-next-action";

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

export interface EditorialGapView {
  id: string;
  title: string;
  description: string;
  actionText: string;
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
  editorialCount: number;
  suggestionCount: number;
  acceptedIncompleteCount: number;
  readinessTitle: string;
  readinessDetail: string;
  recordedStories: RecordedStoryView[];
  directions: FollowupDirectionView[];
  confirmations: ConfirmationView[];
  reviewGaps: ReviewGapView[];
  editorialGaps: EditorialGapView[];
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

function directionView(task: MemoirChapterCompletionTask): FollowupDirectionView {
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
  return blocks.map((block) => draftBlockView(block, storyById, claimById, existing));
}

export function chapterWorkspaceViewPatch(
  workspace: MemoirChapterWorkspace,
  draftBlocks: MemoirDraftBlock[],
): ChapterWorkspaceViewPatch {
  const summary = workspace.completion_summary;
  const recordedStories = workspace.stories
    .filter(storyHasUsableEvidence)
    .map((story) => ({ id: story.id, title: story.title, summary: story.summary }));
  const confirmations = workspace.answer_updates.filter((item) => item.status === "needs_confirmation");
  const currentDraftBlocks = draftBlocks;
  const canAssembleDraft = Boolean(currentDraftBlocks.length) && currentDraftBlocks.every((block) => (
    block.editorial_status === "confirmed"
    && block.freshness_status === "current"
  ));

  return {
    nextAction: chapterNextAction(summary),
    recordedCount: recordedStories.length,
    mustConfirmCount: confirmations.length + summary.review_gaps.length,
    editorialCount: summary.editorial_gaps.length,
    suggestionCount: summary.suggested_count,
    acceptedIncompleteCount: summary.accepted_incomplete_count,
    readinessTitle: summary.headline,
    readinessDetail: summary.detail,
    recordedStories,
    directions: summary.visible_tasks.map(directionView),
    confirmations: confirmations.map((item) => ({
      id: item.id,
      taskId: item.task_id,
      question: item.question,
      answerText: item.answer_text,
    })),
    reviewGaps: summary.review_gaps.map((gap) => ({
      id: gap.id,
      title: gap.title,
      description: gap.description,
      taskId: "",
      resolutionText: "",
    })),
    editorialGaps: summary.editorial_gaps.slice(0, 3).map((gap) => ({
      id: gap.id,
      title: gap.title,
      description: gap.description,
      actionText: "查看并整理正文",
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
