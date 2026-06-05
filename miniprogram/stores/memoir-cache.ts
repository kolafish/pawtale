import type {
  MemoirDraft,
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

function previewText(text: string): string {
  const value = text.trim().replace(/\s+/g, " ");
  return value.length > 80 ? `${value.slice(0, 80)}...` : value;
}

export function updateMemoirCachedStoryDraft(draft: MemoirDraft): void {
  const store = readMemoirStore();
  if (!store.stories) {
    return;
  }

  const stories = store.stories.map((story) => {
    if (story.answer_id !== draft.answer_id) {
      return story;
    }
    return {
      ...story,
      answer_status: draft.status === "accepted" ? "confirmed" : story.answer_status,
      draft_id: draft.id,
      draft_status: draft.status,
      draft_preview: previewText(draft.content_edited || draft.content_raw),
      updated_at: draft.updated_at,
    };
  });
  const summary = store.summary
    ? {
      ...store.summary,
      total_story_count: stories.length,
      pending_review_count: stories.filter((story) => story.draft_status === "draft").length,
      confirmed_story_count: stories.filter((story) => story.draft_status === "accepted").length,
    }
    : undefined;

  writeMemoirDashboardSnapshot({ stories, summary });
}
