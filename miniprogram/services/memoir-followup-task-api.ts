import type {
  MemoirFollowupTask,
  MemoirFollowupTaskActionRequest,
  MemoirFollowupTaskCannotRecallRequest,
  MemoirFollowupTaskDeferRequest,
  MemoirFollowupTaskListResponse,
  MemoirFollowupTaskStatus,
  MemoirFollowupTaskSwitchRequest,
} from "../domain/voice-chat";
import {
  memoirNarrativeProjectPath,
  memoirRequest,
  queryString,
} from "./memoir-api-client";

interface FollowupTaskListOptions {
  status?: MemoirFollowupTaskStatus;
  chapterId?: string;
  limit?: number;
  offset?: number;
}

function followupTaskPath(projectId: string, taskId: string, suffix = ""): string {
  return memoirNarrativeProjectPath(
    projectId,
    `/followup-tasks/${encodeURIComponent(taskId)}${suffix}`,
  );
}

export function listMemoirFollowupTasks(
  projectId: string,
  options: FollowupTaskListOptions = {},
): Promise<MemoirFollowupTaskListResponse> {
  return memoirRequest<MemoirFollowupTaskListResponse>(
    `${memoirNarrativeProjectPath(projectId, "/followup-tasks")}${queryString({
      status: options.status,
      chapter_id: options.chapterId,
      limit: options.limit,
      offset: options.offset,
    })}`,
  );
}

export function getMemoirFollowupTask(
  projectId: string,
  taskId: string,
): Promise<MemoirFollowupTask> {
  return memoirRequest<MemoirFollowupTask>(followupTaskPath(projectId, taskId));
}

export function deferMemoirFollowupTask(
  projectId: string,
  taskId: string,
  data: MemoirFollowupTaskDeferRequest,
): Promise<MemoirFollowupTask> {
  return memoirRequest<MemoirFollowupTask>(followupTaskPath(projectId, taskId, "/defer"), {
    method: "POST",
    data,
  });
}

export function cannotRecallMemoirFollowupTask(
  projectId: string,
  taskId: string,
  data: MemoirFollowupTaskCannotRecallRequest,
): Promise<MemoirFollowupTask> {
  return memoirRequest<MemoirFollowupTask>(followupTaskPath(projectId, taskId, "/cannot-recall"), {
    method: "POST",
    data,
  });
}

export function excludeMemoirFollowupTask(
  projectId: string,
  taskId: string,
  data: MemoirFollowupTaskActionRequest,
): Promise<MemoirFollowupTask> {
  return memoirRequest<MemoirFollowupTask>(followupTaskPath(projectId, taskId, "/exclude"), {
    method: "POST",
    data,
  });
}

export function switchMemoirFollowupTask(
  projectId: string,
  voiceSessionId: string,
  data: MemoirFollowupTaskSwitchRequest,
): Promise<MemoirFollowupTask | null> {
  return memoirRequest<MemoirFollowupTask | null>(
    memoirNarrativeProjectPath(
      projectId,
      `/voice-sessions/${encodeURIComponent(voiceSessionId)}/followup-task/switch`,
    ),
    {
      method: "POST",
      data,
    },
  );
}
