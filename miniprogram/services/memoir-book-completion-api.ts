import type { MemoirBookCompletionOverview } from "../domain/chapter-completion";
import type {
  MemoirChapterCompletionRefreshJob,
  MemoirChapterCompletionRefreshRequest,
} from "../domain/chapter-workspace";
import {
  memoirNarrativeProjectPath,
  memoirRequest,
  withSelectedAIModel,
} from "./memoir-api-client";


function bookCompletionPath(projectId: string, suffix = ""): string {
  return memoirNarrativeProjectPath(projectId, `/book-completion${suffix}`);
}

export function getMemoirBookCompletionOverview(
  projectId: string,
): Promise<MemoirBookCompletionOverview> {
  return memoirRequest<MemoirBookCompletionOverview>(bookCompletionPath(projectId, "/overview"));
}

export function enqueueMemoirChapterCompletionRefresh(
  projectId: string,
  chapterId: string,
  data: MemoirChapterCompletionRefreshRequest = {},
): Promise<MemoirChapterCompletionRefreshJob> {
  return memoirRequest<MemoirChapterCompletionRefreshJob>(
    memoirNarrativeProjectPath(
      projectId,
      `/chapters/${encodeURIComponent(chapterId)}/completion-refresh-jobs`,
    ),
    {
      method: "POST",
      data: withSelectedAIModel({
        include_semantic_audit: data.include_semantic_audit ?? true,
        ai_model: data.ai_model,
      }),
    },
  );
}
