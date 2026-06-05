import type { MemoirInterviewState, MemoirStoryCard, MemoirSummary } from "../domain/memoir";
import { getMemoirStories, getMemoirSummary } from "./memoir";
import { fallbackSummary, readMemoirDashboardSnapshot } from "../stores/memoir-cache";

export async function loadMemoirSummary(interview: MemoirInterviewState): Promise<MemoirSummary> {
  try {
    return await getMemoirSummary();
  } catch {
    return readMemoirDashboardSnapshot().summary || fallbackSummary(interview);
  }
}

export async function loadMemoirStories(): Promise<MemoirStoryCard[]> {
  try {
    return (await getMemoirStories()).stories;
  } catch (error) {
    const snapshot = readMemoirDashboardSnapshot();
    if (snapshot.hasStories) {
      return snapshot.stories;
    }
    throw error;
  }
}
