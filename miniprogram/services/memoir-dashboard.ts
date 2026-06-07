import type {
  MemoirCoverageItem,
  MemoirEventMemory,
  MemoirInterviewState,
  MemoirMemoryStats,
  MemoirOpenQuestion,
  MemoirOutput,
  MemoirPersonMemory,
  MemoirPlaceMemory,
  MemoirQuestionCandidate,
  MemoirSessionSummary,
  MemoirSummary,
  MemoirThemeMemory,
  Project,
} from "../domain/memoir";
import { ensureLocalDevSession } from "./auth";
import { getMemoirDashboard } from "./memoir";
import { writeMemoirDashboardSnapshot } from "../stores/memoir-cache";

export interface MemoirDashboardLoadOptions {
  includeProject?: boolean;
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
  memoryStats?: MemoirMemoryStats;
}

export async function loadMemoirDashboard(options: MemoirDashboardLoadOptions = {}): Promise<MemoirDashboardData> {
  void options;
  await ensureLocalDevSession();
  const dashboard = await getMemoirDashboard();
  const sessionSummary = dashboard.session_summary;
  const people = dashboard.people;
  const events = dashboard.events;
  const places = dashboard.places;
  const themes = dashboard.themes;
  const openQuestions = dashboard.open_questions;
  const coverage = dashboard.coverage;
  const candidates = dashboard.candidates;
  const outputs = dashboard.outputs;
  const memoryStats = dashboard.memory_stats;

  writeMemoirDashboardSnapshot({
    project: dashboard.project,
    interview: dashboard.interview,
    summary: dashboard.summary,
    sessionSummary,
    people,
    events,
    places,
    themes,
    openQuestions,
    coverage,
    candidates,
    outputs,
    memoryStats,
  });

  return {
    project: dashboard.project,
    interview: dashboard.interview,
    summary: dashboard.summary,
    sessionSummary,
    people,
    events,
    places,
    themes,
    openQuestions,
    coverage,
    candidates,
    outputs,
    memoryStats,
  };
}
