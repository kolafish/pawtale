import type {
  MemoirCompletionOutcome,
  MemoirCompletionItem,
  MemoirCompletionItemActionRequest,
  MemoirCompletionItemDeferRequest,
  MemoirCompletionItemSwitchRequest,
} from "../domain/voice-chat";
import {
  memoirNarrativeProjectPath,
  memoirRequest,
} from "./memoir-api-client";

function completionItemPath(projectId: string, itemId: string, suffix = ""): string {
  return memoirNarrativeProjectPath(
    projectId,
    `/completion-items/${encodeURIComponent(itemId)}${suffix}`,
  );
}

export function getMemoirCompletionItem(
  projectId: string,
  itemId: string,
): Promise<MemoirCompletionItem> {
  return memoirRequest<MemoirCompletionItem>(completionItemPath(projectId, itemId));
}

export function resolveMemoirCompletionItem(
  projectId: string,
  itemId: string,
  data: MemoirCompletionItemActionRequest,
): Promise<MemoirCompletionItem> {
  return memoirRequest<MemoirCompletionItem>(
    completionItemPath(projectId, itemId, "/resolve"),
    { method: "POST", data },
  );
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

export function deferMemoirCompletionItem(
  projectId: string,
  itemId: string,
  data: MemoirCompletionItemDeferRequest,
): Promise<MemoirCompletionItem> {
  return memoirRequest<MemoirCompletionItem>(completionItemPath(projectId, itemId, "/defer"), {
    method: "POST",
    data,
  });
}

export function markMemoirCompletionItemUnableToAnswer(
  projectId: string,
  itemId: string,
  data: MemoirCompletionItemActionRequest,
): Promise<MemoirCompletionItem> {
  return memoirRequest<MemoirCompletionItem>(
    completionItemPath(projectId, itemId, "/unable-to-answer"),
    {
      method: "POST",
      data,
    },
  );
}

export function switchMemoirCompletionItem(
  projectId: string,
  voiceSessionId: string,
  data: MemoirCompletionItemSwitchRequest,
): Promise<MemoirCompletionItem | null> {
  return memoirRequest<MemoirCompletionItem | null>(
    memoirNarrativeProjectPath(
      projectId,
      `/voice-sessions/${encodeURIComponent(voiceSessionId)}/completion-item/switch`,
    ),
    {
      method: "POST",
      data,
    },
  );
}
