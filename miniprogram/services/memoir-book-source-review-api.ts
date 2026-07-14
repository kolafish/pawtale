import type {
  MemoirBookSourceReviewConfirmRequest,
  MemoirBookSourceReviewConfirmResponse,
  MemoirBookSourceReviewListResponse,
} from "../domain/voice-chat";
import { memoirProjectPath, memoirRequest } from "./memoir-api-client";


function sourceReviewPath(projectId: string, suffix = ""): string {
  return memoirProjectPath(projectId, `/book-source-review-items${suffix}`);
}


export function listMemoirBookSourceReviewItems(
  projectId: string,
): Promise<MemoirBookSourceReviewListResponse> {
  return memoirRequest<MemoirBookSourceReviewListResponse>(sourceReviewPath(projectId));
}


export function confirmMemoirBookSourceReviewItem(
  projectId: string,
  eventId: string,
  data: MemoirBookSourceReviewConfirmRequest,
): Promise<MemoirBookSourceReviewConfirmResponse> {
  return memoirRequest<MemoirBookSourceReviewConfirmResponse>(
    sourceReviewPath(projectId, `/${encodeURIComponent(eventId)}/confirm`),
    { method: "POST", data },
  );
}
