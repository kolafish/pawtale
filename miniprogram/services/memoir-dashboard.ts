import type {
  MemoirCoverageItem,
  MemoirEventMemory,
  MemoirInterviewState,
  MemoirOpenQuestion,
  MemoirOutput,
  MemoirPersonMemory,
  MemoirPlaceMemory,
  MemoirQuestionCandidate,
  MemoirSessionSummary,
  MemoirStoryCard,
  MemoirSummary,
  MemoirThemeMemory,
  Project,
} from "../domain/memoir";
import { ensureLocalDevSession } from "./auth";
import {
  getCoverageMap,
  getCurrentSession,
  getMemoirProject,
  getMemoirStories,
  getMemoirSummary,
  getSessionSummary,
  listEventMemories,
  listMemoirOutputs,
  listOpenQuestions,
  listPeopleMemories,
  listPlaceMemories,
  listQuestionCandidates,
  listThemeMemories,
} from "./memoir";
import {
  fallbackSummary,
  readMemoirDashboardSnapshot,
  writeMemoirDashboardSnapshot,
} from "../stores/memoir-cache";

export interface MemoirDashboardLoadOptions {
  includeProject?: boolean;
  includeStories?: boolean;
  includeMemory?: boolean;
}

export interface MemoirDashboardData {
  project?: Project;
  interview: MemoirInterviewState;
  summary: MemoirSummary;
  sessionSummary?: MemoirSessionSummary | null;
  people: MemoirPersonMemory[];
  events: MemoirEventMemory[];
  places: MemoirPlaceMemory[];
  themes: MemoirThemeMemory[];
  openQuestions: MemoirOpenQuestion[];
  coverage: MemoirCoverageItem[];
  candidates: MemoirQuestionCandidate[];
  outputs: MemoirOutput[];
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
  const memory = options.includeMemory
    ? await Promise.all([
      getSessionSummary(interview.session.id),
      listPeopleMemories(),
      listEventMemories(),
      listPlaceMemories(),
      listThemeMemories(),
      listOpenQuestions(),
      getCoverageMap(),
      listQuestionCandidates(),
      listMemoirOutputs(),
    ])
    : [
      snapshot.sessionSummary,
      snapshot.people,
      snapshot.events,
      snapshot.places,
      snapshot.themes,
      snapshot.openQuestions,
      snapshot.coverage,
      snapshot.candidates,
      snapshot.outputs,
    ] as const;
  const stories = options.includeStories ? await loadMemoirStories() : snapshot.stories;
  const hasStoryData = options.includeStories ? true : snapshot.hasStories;
  const [
    sessionSummary,
    people,
    events,
    places,
    themes,
    openQuestions,
    coverage,
    candidates,
    outputs,
  ] = memory;

  writeMemoirDashboardSnapshot({
    project,
    interview,
    summary,
    sessionSummary,
    people,
    events,
    places,
    themes,
    openQuestions,
    coverage,
    candidates,
    outputs,
    stories: options.includeStories ? stories : undefined,
  });

  return {
    project,
    interview,
    summary,
    sessionSummary,
    people,
    events,
    places,
    themes,
    openQuestions,
    coverage,
    candidates,
    outputs,
    stories,
    hasStoryData,
  };
}
