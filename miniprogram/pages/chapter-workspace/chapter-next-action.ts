import type {
  MemoirChapterCompletionNextActionKind,
  MemoirChapterCompletionSummary,
} from "../../domain/chapter-completion";


export type ChapterNextActionKind = MemoirChapterCompletionNextActionKind;

export interface ChapterNextAction {
  kind: ChapterNextActionKind;
  eyebrow: string;
  title: string;
  detail: string;
  actionText: string;
  disabled: boolean;
  taskId?: string;
}

export const LOADING_NEXT_ACTION: ChapterNextAction = {
  kind: "wait",
  eyebrow: "本章下一步",
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
    eyebrow: summary.next_action.eyebrow,
    title: summary.next_action.title,
    detail: summary.next_action.detail,
    actionText: summary.next_action.action_text,
    disabled: summary.next_action.disabled,
    taskId: summary.next_action.task_id || undefined,
  };
}
