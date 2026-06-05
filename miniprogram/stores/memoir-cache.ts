import type {
  MemoirInterviewState,
  MemoirStoryCard,
  MemoirSummary,
  Project,
} from "../domain/memoir";
import { readMemoirStore, writeMemoirStore, type MemoirSessionStore } from "./session";

export interface MemoirDashboardSnapshot {
  project?: Project;
  interview?: MemoirInterviewState;
  summary?: MemoirSummary;
  stories: MemoirStoryCard[];
  hasStories: boolean;
}

export function answeredQuestionCount(interview: MemoirInterviewState): number {
  return interview.questions.filter((question) => question.status === "answered").length;
}

export function fallbackSummary(interview: MemoirInterviewState): MemoirSummary {
  const totalQuestionCount = interview.questions.length;
  const answeredCount = answeredQuestionCount(interview);
  return {
    total_story_count: answeredCount,
    weekly_story_count: answeredCount,
    pending_review_count: interview.latest_draft?.status === "draft" ? 1 : 0,
    confirmed_story_count: interview.latest_draft?.status === "accepted" ? 1 : 0,
    total_question_count: totalQuestionCount,
    completed_question_count: interview.completed_question_count,
    progress_percent: totalQuestionCount
      ? Math.round((interview.completed_question_count * 100) / totalQuestionCount)
      : 0,
  };
}

export function readMemoirDashboardSnapshot(): MemoirDashboardSnapshot {
  const store = readMemoirStore();
  return {
    project: store.project,
    interview: store.interview,
    summary: store.summary || (store.interview ? fallbackSummary(store.interview) : undefined),
    stories: store.stories || [],
    hasStories: store.stories !== undefined,
  };
}

export function writeMemoirDashboardSnapshot(snapshot: Partial<MemoirDashboardSnapshot>): void {
  const patch: MemoirSessionStore = {
    cached_at: new Date().toISOString(),
  };
  if (snapshot.project !== undefined) {
    patch.project = snapshot.project;
  }
  if (snapshot.interview !== undefined) {
    patch.interview = snapshot.interview;
  }
  if (snapshot.summary !== undefined) {
    patch.summary = snapshot.summary;
  }
  if (snapshot.stories !== undefined) {
    patch.stories = snapshot.stories;
  }
  writeMemoirStore(patch);
}
