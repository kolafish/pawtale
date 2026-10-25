import type { MemoirShareLikeTarget } from "../domain/memoir-share-likes";
import { requestWithBackendSession } from "./auth";
import { downloadAuthenticatedFile } from "./authenticated-media";
import { cleanBaseUrl, readBackendConfig } from "./request";
import { memoirProjectPath, memoirRequest, queryString } from "./memoir-api-client";

export interface ShareResponseItem {
  id: string; target_kind: "book" | "chapter" | "story" | "video";
  target_title: string; display_name: string; content: string;
  voice_capture_id: string | null; created_at: string; read_at: string | null; adopted_at: string | null;
  adopted_utterance_id: string | null;
}
export interface ShareResponseList { items: ShareResponseItem[]; next_before_id: string | null; unread_count: number }
const base = "/api/mobile/memoir/share-responses";
export function submitShareResponse(target: MemoirShareLikeTarget, content: string, requestId: string, voiceCaptureId = ""): Promise<ShareResponseItem> {
  return requestWithBackendSession<ShareResponseItem>(base, { method: "POST", data: { ...target, content, request_id: requestId, ...(voiceCaptureId ? { voice_capture_id: voiceCaptureId } : {}) }, timeoutProfile: "mutation" });
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

export async function startShareResponseVoice(target: MemoirShareLikeTarget): Promise<{ captureId: string; gatewayUrl: string }> {
  const result = await requestWithBackendSession<{capture_id: string}>(`${base}/voice`, { method: "POST", data: target });
  const config = readBackendConfig();
  if (!config.wsBaseUrl) throw new Error("暂时无法连接语音服务");
  return { captureId: result.capture_id, gatewayUrl: `${cleanBaseUrl(config.wsBaseUrl)}${base}/voice/${encodeURIComponent(result.capture_id)}` };
}
export function downloadShareResponseAudio(responseId: string): Promise<string> {
  const config = readBackendConfig();
  if (!config.httpBaseUrl) throw new Error("暂时无法读取原音");
  return downloadAuthenticatedFile(`${cleanBaseUrl(config.httpBaseUrl)}${base}/${encodeURIComponent(responseId)}/audio`, {
    failureMessage: "原音暂时无法播放", domainLabel: "家人原音",
  });
}
