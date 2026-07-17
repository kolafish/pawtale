import type { MemoirChapterWorkspace } from "../../domain/chapter-workspace";
import { chapterNextAction, type ChapterNextAction } from "./chapter-next-action";

export interface ChapterWorkspaceViewPatch {
  nextAction: ChapterNextAction;
  readinessTitle: string;
  readinessDetail: string;
  canConfirmChapter: boolean;
}

export function chapterWorkspaceViewPatch(
  workspace: MemoirChapterWorkspace,
): ChapterWorkspaceViewPatch {
  const summary = workspace.completion_summary;
  return {
    nextAction: chapterNextAction(summary),
    readinessTitle: summary.headline,
    readinessDetail: summary.detail,
    canConfirmChapter:
      summary.state === "draft_ready"
      && Boolean(workspace.draft_version_id)
      && Boolean(workspace.draft_content.trim()),
  };
}
