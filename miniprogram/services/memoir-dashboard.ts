import type { MemoirInterviewState, MemoirStoryCard, MemoirSummary, Project } from "../domain/memoir";
import { ensureLocalDevSession } from "./auth";
import { getCurrentSession, getMemoirProject, getMemoirStories, getMemoirSummary } from "./memoir";
import {
  fallbackSummary,
  readMemoirDashboardSnapshot,
  writeMemoirDashboardSnapshot,
} from "../stores/memoir-cache";

export interface MemoirDashboardLoadOptions {
  includeProject?: boolean;
  includeStories?: boolean;
}

export interface MemoirDashboardData {
  project?: Project;
  interview: MemoirInterviewState;
  summary: MemoirSummary;
  stories: MemoirStoryCard[];
  hasStoryData: boolean;
}

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

export async function loadMemoirDashboard(options: MemoirDashboardLoadOptions = {}): Promise<MemoirDashboardData> {
  await ensureLocalDevSession();
  const snapshot = readMemoirDashboardSnapshot();
  const project = options.includeProject ? await getMemoirProject() : snapshot.project;
  const interview = await getCurrentSession();
  const summary = await loadMemoirSummary(interview);
  const stories = options.includeStories ? await loadMemoirStories() : snapshot.stories;
  const hasStoryData = options.includeStories ? true : snapshot.hasStories;

  writeMemoirDashboardSnapshot({
    project,
    interview,
    summary,
    stories: options.includeStories ? stories : undefined,
  });

  return {
    project,
    interview,
    summary,
    stories,
    hasStoryData,
  };
}
