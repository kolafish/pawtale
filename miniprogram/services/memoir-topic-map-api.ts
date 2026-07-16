import type {
  MemoirTopicMap,
  MemoirTopicMapItem,
  MemoirTopicMapUserResolution,
} from "../domain/topic-map";
import {
  memoirProjectPath,
  memoirRequest,
} from "./memoir-api-client";


export function getMemoirTopicMap(projectId: string): Promise<MemoirTopicMap> {
  return memoirRequest<MemoirTopicMap>(
    memoirProjectPath(projectId, "/topic-map"),
  );
}

export function updateMemoirTopicMapResolution(
  projectId: string,
  itemId: string,
  userResolution: MemoirTopicMapUserResolution,
): Promise<MemoirTopicMapItem> {
  return memoirRequest<MemoirTopicMapItem>(
    memoirProjectPath(
      projectId,
      `/topic-map/items/${encodeURIComponent(itemId)}/resolution`,
    ),
    {
      method: "PATCH",
      data: {
        user_resolution: userResolution,
      },
    },
  );
}
