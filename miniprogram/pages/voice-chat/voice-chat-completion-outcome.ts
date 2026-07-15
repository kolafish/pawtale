import type { MemoirCompletionOutcome } from "../../domain/voice-chat";

export interface CompletionOutcomeDialog {
  title: string;
  content: string;
  confirmText: string;
  action: "review" | "back";
}

export function completionOutcomeDialog(
  outcome: MemoirCompletionOutcome,
): CompletionOutcomeDialog {
  const reason = outcome.reason.trim();
  if (outcome.status === "incorporated") {
    return {
      title: "已经收入章节素材",
      content: outcome.message,
      confirmText: "返回章节",
      action: "back",
    };
  }
  if (outcome.status === "context_recorded") {
    return {
      title: "章节说明已经保存",
      content: outcome.message,
      confirmText: "返回章节",
      action: "back",
    };
  }
  if (outcome.status === "needs_confirmation") {
    return {
      title: "还需要确认一下",
      content: outcome.message,
      confirmText: outcome.review_event_id ? "去确认" : "返回章节",
      action: outcome.review_event_id ? "review" : "back",
    };
  }
  if (outcome.status === "not_used") {
    return {
      title: "这段没有用于书稿",
      content: reason ? `${outcome.message}\n原因：${reason}` : outcome.message,
      confirmText: "返回章节重试",
      action: "back",
    };
  }
  return {
    title: "回答已经保存",
    content: outcome.message,
    confirmText: "返回章节",
    action: "back",
  };
}
