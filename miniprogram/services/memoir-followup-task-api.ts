import type {
  MemoirCompletionOutcome,
  MemoirFollowupTask,
  MemoirFollowupTaskActionRequest,
  MemoirFollowupTaskDeferRequest,
  MemoirFollowupTaskSwitchRequest,
} from "../domain/voice-chat";
import {
  memoirNarrativeProjectPath,
  memoirRequest,
} from "./memoir-api-client";

function followupTaskPath(projectId: string, taskId: string, suffix = ""): string {
  return memoirNarrativeProjectPath(
    projectId,
    `/followup-tasks/${encodeURIComponent(taskId)}${suffix}`,
  );
}

export function getMemoirFollowupTask(
  projectId: string,
  taskId: string,
): Promise<MemoirFollowupTask> {
  return memoirRequest<MemoirFollowupTask>(followupTaskPath(projectId, taskId));
}

export function getMemoirCompletionOutcome(
  projectId: string,
  voiceSessionId: string,
): Promise<MemoirCompletionOutcome> {
  return memoirRequest<MemoirCompletionOutcome>(
    memoirNarrativeProjectPath(
      projectId,
      `/voice-sessions/${encodeURIComponent(voiceSessionId)}/completion-outcome`,
    ),
  );
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

export function markMemoirFollowupTaskUnableToAnswer(
  projectId: string,
  taskId: string,
  data: MemoirFollowupTaskActionRequest,
): Promise<MemoirFollowupTask> {
  return memoirRequest<MemoirFollowupTask>(
    followupTaskPath(projectId, taskId, "/unable-to-answer"),
    {
      method: "POST",
      data,
    },
  );
}

export function stopAskingMemoirFollowupTask(
  projectId: string,
  taskId: string,
  data: MemoirFollowupTaskActionRequest,
): Promise<MemoirFollowupTask> {
  return memoirRequest<MemoirFollowupTask>(
    followupTaskPath(projectId, taskId, "/stop-asking"),
    {
      method: "POST",
      data,
    },
  );
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
