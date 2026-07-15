import type { MemoirChapterWorkspace } from "../../domain/chapter-workspace";
import type { MemoirDraftBlock } from "../../domain/draft-blocks";
import type { MemoirFollowupTask } from "../../domain/voice-chat";


export type ChapterNextActionKind =
  | "create_direction"
  | "confirm_direction"
  | "start_audit"
  | "wait"
  | "continue_fact_confirmation"
  | "open_followups"
  | "open_checks"
  | "generate_draft"
  | "open_drafts"
  | "complete";

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
  workspace: MemoirChapterWorkspace | null,
  followupTasks: MemoirFollowupTask[],
  draftBlocks: MemoirDraftBlock[],
): ChapterNextAction {
  if (!workspace) {
    return LOADING_NEXT_ACTION;
  }
  if (!workspace.contract) {
    return {
      kind: "create_direction",
      eyebrow: "先定方向",
      title: "先确定这章主要写什么",
      detail: "系统会根据现有素材先整理出本章主题和内容顺序，再请您确认。",
      actionText: "整理本章方向",
      disabled: false,
    };
  }
  if (workspace.contract.status !== "confirmed") {
    return {
      kind: "confirm_direction",
      eyebrow: "确认本章方向",
      title: workspace.contract.objective || "这个方向是否符合您的想法？",
      detail: workspace.contract.reader_takeaway || "确认后就可以按这个方向继续整理。",
      actionText: "这个方向可以",
      disabled: false,
    };
  }
  const pendingFactConfirmation = workspace.answer_updates.find(
    (item) => item.status === "needs_confirmation",
  );
  if (pendingFactConfirmation) {
    return {
      kind: "continue_fact_confirmation",
      eyebrow: "还需要您说明一句",
      title: pendingFactConfirmation.question,
      detail: "刚才的回答还不能唯一对应到书里的人物或事实，直接再说明一句即可。",
      actionText: "继续说明",
      disabled: false,
      taskId: pendingFactConfirmation.task_id,
    };
  }
  const audit = workspace.latest_audit_job;
  if (!audit) {
    return {
      kind: "start_audit",
      eyebrow: "看看还缺什么",
      title: "整理这章还缺什么",
      detail: "系统会查看现有内容，然后只给出一个最值得处理的下一步。",
      actionText: "开始整理",
      disabled: false,
    };
  }
  if (audit.status === "pending" || audit.status === "running") {
    return {
      kind: "wait",
      eyebrow: "正在整理",
      title: "正在查看本章还缺什么",
      detail: "整理完成后，这里会自动显示下一步。",
      actionText: "",
      disabled: true,
    };
  }
  if (audit.status === "failed" || audit.status === "skipped") {
    return {
      kind: "start_audit",
      eyebrow: "需要重试",
      title: "上次没有整理完成",
      detail: audit.error_message || "已有正文不会受影响，可以重新整理。",
      actionText: "重新整理",
      disabled: false,
    };
  }
  const mustHandle = workspace.gaps.must_handle[0];
  if (mustHandle) {
    return {
      kind: "open_checks",
      eyebrow: "先确认一件事",
      title: mustHandle.title,
      detail: mustHandle.description,
      actionText: "处理这件事",
      disabled: false,
    };
  }
  const task = followupTasks.find((item) => item.status === "in_progress")
    || followupTasks.find((item) => item.status === "pending")
    || followupTasks.find((item) => item.status === "deferred");
  if (task) {
    return {
      kind: "open_followups",
      eyebrow: "选择一个方向继续讲",
      title: task.question,
      detail: "下方会同时列出最多三个方向，您可以自己决定先讲哪一个。",
      actionText: task.status === "in_progress" ? "继续刚才的回答" : "从这个方向开始",
      disabled: false,
      taskId: task.id,
    };
  }
  if (!draftBlocks.length) {
    return {
      kind: "generate_draft",
      eyebrow: "开始写正文",
      title: "现有内容已经可以整理成这一章",
      detail: "系统会按本章方向生成初稿，生成后再由您通读确认。",
      actionText: "生成本章初稿",
      disabled: false,
    };
  }
  const draftNeedsReview = draftBlocks.some((block) => (
    block.freshness_status === "stale" || block.editorial_status === "draft"
  ));
  if (draftNeedsReview) {
    return {
      kind: "open_drafts",
      eyebrow: "通读正文",
      title: "本章正文需要您看一遍",
      detail: "只需关注内容是否准确、是否像讲述者本人，不必理解内部状态。",
      actionText: "查看本章正文",
      disabled: false,
    };
  }
  return {
    kind: "complete",
    eyebrow: "本章已整理",
    title: "这一章目前没有必须处理的内容",
    detail: "以后增加新素材时，系统会重新给出下一步。",
    actionText: "",
    disabled: true,
  };
}
