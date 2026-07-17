import type {
  MemoirChapterCompletionNextActionKind,
  MemoirChapterCompletionSummary,
} from "../../domain/chapter-completion";


export type ChapterNextActionKind = MemoirChapterCompletionNextActionKind;

export interface ChapterNextAction {
  kind: ChapterNextActionKind;
  title: string;
  detail: string;
  actionText: string;
  disabled: boolean;
  itemId?: string;
}

export const LOADING_NEXT_ACTION: ChapterNextAction = {
  kind: "wait",
  title: "正在整理本章",
  detail: "马上就会告诉您现在最值得处理的一件事。",
  actionText: "",
  disabled: true,
};

export function chapterNextAction(
  summary: MemoirChapterCompletionSummary,
): ChapterNextAction {
  return {
    kind: summary.next_action.kind,
    title: summary.next_action.title,
    detail: summary.next_action.detail,
    actionText: summary.next_action.action_text,
    disabled: summary.next_action.disabled,
    itemId: summary.next_action.item_id || undefined,
  };
}
