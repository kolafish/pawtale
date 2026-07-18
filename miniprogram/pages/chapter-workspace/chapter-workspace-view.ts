import type { MemoirChapterWorkspace } from "../../domain/chapter-workspace";
import { chapterNextAction, type ChapterNextAction } from "./chapter-next-action";

export interface ChapterWorkspaceViewPatch {
  nextAction: ChapterNextAction;
  readinessTitle: string;
  readinessDetail: string;
  rewriteNoticeTitle: string;
  rewriteNoticeDetail: string;
  canConfirmChapter: boolean;
}

function rewriteNoticeTitle(workspace: MemoirChapterWorkspace): string {
  const completionCount = workspace.pending_completion_source_count || 0;
  const newCount = workspace.pending_new_source_count || 0;
  if (completionCount && newCount) {
    return `${completionCount} 条补充回答和 ${newCount} 条新增讲述尚未写入正文`;
  }
  if (completionCount) {
    return `${completionCount} 条补充回答尚未写入正文`;
  }
  if (newCount) {
    return `${newCount} 条新增讲述尚未写入正文`;
  }
  return "本章素材已经变化";
}

export function chapterWorkspaceViewPatch(
  workspace: MemoirChapterWorkspace,
): ChapterWorkspaceViewPatch {
  const summary = workspace.completion_summary;
  return {
    nextAction: chapterNextAction(summary),
    readinessTitle: summary.headline,
    readinessDetail: summary.detail,
    rewriteNoticeTitle: rewriteNoticeTitle(workspace),
    rewriteNoticeDetail: workspace.can_rewrite_draft
      ? "可以继续补充，也可以先用本章全部现有素材重新生成完整正文；旧版本仍会保留。"
      : "请先完成上方必须确认的事项，再重新生成正文。",
    canConfirmChapter:
      summary.state === "draft_ready"
      && Boolean(workspace.draft_version_id)
      && Boolean(workspace.draft_content.trim()),
  };
}
