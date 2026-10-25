import type { MemoirShareLikeTarget } from "../domain/memoir-share-likes";
import { requestWithBackendSession } from "./auth";
import { memoirProjectPath, memoirRequest, queryString } from "./memoir-api-client";

export interface ShareResponseItem {
  id: string; target_kind: "book" | "chapter" | "story" | "video";
  target_title: string; display_name: string; content: string;
  created_at: string; read_at: string | null; adopted_at: string | null;
  adopted_utterance_id: string | null;
}
export interface ShareResponseList { items: ShareResponseItem[]; next_before_id: string | null; unread_count: number }
const base = "/api/mobile/memoir/share-responses";
export function submitShareResponse(target: MemoirShareLikeTarget, content: string, requestId: string): Promise<ShareResponseItem> {
  return requestWithBackendSession<ShareResponseItem>(base, { method: "POST", data: { ...target, content, request_id: requestId }, timeoutProfile: "mutation" });
}
export function listShareResponses(projectId: string, beforeId = ""): Promise<ShareResponseList> {
  return memoirRequest<ShareResponseList>(memoirProjectPath(projectId, `/share-responses${queryString({ before_id: beforeId })}`));
}
export function readShareResponse(id: string): Promise<ShareResponseItem> {
  return memoirRequest<ShareResponseItem>(`${base}/${encodeURIComponent(id)}/read`, { method: "POST", invalidatesSnapshots: false });
}
export function adoptShareResponse(id: string): Promise<ShareResponseItem> {
  return memoirRequest<ShareResponseItem>(`${base}/${encodeURIComponent(id)}/adopt`, { method: "POST" });
}
