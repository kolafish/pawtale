import type { MemoirChapterWorkspace } from "../../domain/chapter-workspace";
import type { MemoirDraftBlock } from "../../domain/draft-blocks";
import { chapterNextAction, type ChapterNextAction } from "./chapter-next-action";


export interface DraftBlockView extends MemoirDraftBlock {
  busy: boolean;
  sectionTitle: string;
  statusText: string;
}

export interface ConfirmationView {
  id: string;
  question: string;
  detail: string;
  statement: string;
}

export interface ChapterWorkspaceViewPatch {
  nextAction: ChapterNextAction;
  readinessTitle: string;
  readinessDetail: string;
  confirmation: ConfirmationView | null;
  canConfirmChapter: boolean;
}

function draftBlockView(
  block: MemoirDraftBlock,
  existing: DraftBlockView[],
): DraftBlockView {
  const sectionTitle = ({
    opening: "开头",
    factual: "正文",
    transition: "衔接",
    reflection: "回望与感受",
    closing: "结尾",
  })[block.block_role];
  let statusText = "可以修改";
  if (block.freshness_status === "stale") {
    statusText = block.locked ? "原文已保护，需重新整理" : "等待重新整理";
  } else if (block.locked) {
    statusText = "原文已保护";
  }
  return {
    ...block,
    busy: existing.find((item) => item.id === block.id)?.busy || false,
    sectionTitle,
    statusText,
  };
}

export function draftBlockViews(
  blocks: MemoirDraftBlock[],
  existing: DraftBlockView[] = [],
): DraftBlockView[] {
  return blocks.map((block) => draftBlockView(block, existing));
}

export function chapterWorkspaceViewPatch(
  workspace: MemoirChapterWorkspace,
  draftBlocks: DraftBlockView[],
  currentConfirmation: ConfirmationView | null = null,
): ChapterWorkspaceViewPatch {
  const summary = workspace.completion_summary;
  const confirmation = summary.confirmation
    ? {
      id: summary.confirmation.id,
      question: summary.confirmation.question,
      detail: summary.confirmation.detail,
      statement: currentConfirmation?.id === summary.confirmation.id
        ? currentConfirmation.statement
        : "",
    }
    : null;
  return {
    nextAction: chapterNextAction(summary),
    readinessTitle: summary.headline,
    readinessDetail: summary.detail,
    confirmation,
    canConfirmChapter:
      summary.state === "draft_ready"
      && Boolean(draftBlocks.length)
      && draftBlocks.every((block) => block.freshness_status === "current"),
  };
}
