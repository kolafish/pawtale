import type {
  MemoirAnswerSession,
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
import { getMemoirIdentity } from "./auth";
import { getMemoirDashboard } from "./memoir";
import { writeMemoirDashboardSnapshot } from "../stores/memoir-cache";
import { writeMemoirAnswerSessionDraftFromRemote } from "../stores/memoir-answer-session-drafts";
import { cacheIdentityFromUser, ensureMemoirStoreIdentity } from "../stores/session";

export interface MemoirDashboardData {
  project?: Project;
  interview: MemoirInterviewState;
  activeAnswerSession?: MemoirAnswerSession | null;
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

export async function loadMemoirDashboard(): Promise<MemoirDashboardData> {
  const user = await getMemoirIdentity();
  const dashboard = await getMemoirDashboard();
  ensureMemoirStoreIdentity(cacheIdentityFromUser(user, dashboard.project));
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
  const activeAnswerSession = dashboard.active_answer_session || null;
  if (activeAnswerSession) {
    writeMemoirAnswerSessionDraftFromRemote(activeAnswerSession);
  }

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
    activeAnswerSession,
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
