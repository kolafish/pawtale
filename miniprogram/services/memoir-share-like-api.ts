import type { MemoirShareLikeActivity, MemoirShareLikes, MemoirShareLikeTarget } from "../domain/memoir-share-likes";
import { memoirProjectPath, memoirRequest, queryString } from "./memoir-api-client";
import { requestWithBackendSession } from "./auth";
import { request } from "./request";

export function readMemoirShareLikes(target: MemoirShareLikeTarget): Promise<MemoirShareLikes> {
  return request<MemoirShareLikes>("/api/mobile/memoir/share-likes/read", { method: "POST", data: target });
}

export function setMemoirShareLike(target: MemoirShareLikeTarget, liked: boolean): Promise<MemoirShareLikes> {
  return requestWithBackendSession<MemoirShareLikes>("/api/mobile/memoir/share-likes", {
    method: "PUT",
    data: { ...target, liked },
    timeoutProfile: "mutation",
  });
}

export function listMemoirShareLikeActivity(projectId: string, after = ""): Promise<MemoirShareLikeActivity> {
  return memoirRequest<MemoirShareLikeActivity>(memoirProjectPath(projectId, `/share-likes${queryString({ after })}`));
}
