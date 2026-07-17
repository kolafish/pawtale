import type { MemoirCompletionOutcome } from "../../domain/voice-chat";

export interface CompletionOutcomeDialog {
  title: string;
  content: string;
  confirmText: string;
  action: "back";
}

export function completionOutcomeDialog(
  outcome: MemoirCompletionOutcome,
): CompletionOutcomeDialog {
  if (outcome.status === "incorporated") {
    return {
      title: "已经收入章节素材",
      content: `${outcome.message}\n回到章节工作台整理正文后，书稿才会更新。`,
      confirmText: "返回章节",
      action: "back",
    };
  }
  if (outcome.status === "not_used") {
    return {
      title: "这段没有用于书稿",
      content: outcome.message,
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
