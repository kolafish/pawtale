import type {
  MemoirCompletionIssue,
  MemoirCompletionIssueCloseRequest,
  MemoirCompletionIssueCollection,
} from "../domain/memoir-completion-issue";
import {
  memoirNarrativeProjectPath,
  memoirRequest,
} from "./memoir-api-client";

function completionIssuePath(projectId: string, issueId: string, suffix = ""): string {
  return memoirNarrativeProjectPath(
    projectId,
    `/completion-issues/${encodeURIComponent(issueId)}${suffix}`,
  );
}

export function listMemoirCompletionIssues(
  projectId: string,
  options: {
    chapterId?: string;
    includeClosed?: boolean;
  } = {},
): Promise<MemoirCompletionIssueCollection> {
  const query = [
    options.chapterId
      ? `chapter_id=${encodeURIComponent(options.chapterId)}`
      : "",
    options.includeClosed ? "include_closed=true" : "",
  ].filter(Boolean).join("&");
  return memoirRequest<MemoirCompletionIssueCollection>(
    memoirNarrativeProjectPath(
      projectId,
      `/completion-issues${query ? `?${query}` : ""}`,
    ),
  );
}

export function closeMemoirCompletionIssue(
  projectId: string,
  issueId: string,
  data: MemoirCompletionIssueCloseRequest,
): Promise<MemoirCompletionIssue> {
  return memoirRequest<MemoirCompletionIssue>(
    completionIssuePath(projectId, issueId, "/close"),
    {
      method: "POST",
      data,
    },
  );
}
